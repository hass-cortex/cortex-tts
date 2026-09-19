"""TTS platform for Cortex TTS — one entity per usable model."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, TypedDict

import aiohttp
from homeassistant.components.tts import (
    ATTR_AUDIO_OUTPUT,
    ATTR_PREFERRED_FORMAT,
    ATTR_VOICE,
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
    Voice,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import CortexTTSAuthError, CortexTTSClient, CortexTTSError, LiveSession
from .const import (
    CONF_CONVERT_SCRIPT,
    CONF_EXPAND_NUMBERS,
    CONF_NORMALIZE_TEXT,
    CONF_STYLE_INSTRUCTION,
    CONF_TAIWAN_READINGS,
    DOMAIN,
    FIRST_AUDIO_FIELDS,
    STREAM_FORMAT,
    STREAM_WHOLE,
    TEXT_FIELDS,
)
from .entity import device_for
from .entity_setup import async_setup_dynamic_models
from .models import (
    CortexTTSRuntimeData,
    ModelInfo,
    SpeechStats,
    VoiceInfo,
    entity_unique_id,
    stream_mode,
)

if TYPE_CHECKING:
    from . import CortexTTSConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Home Assistant scores a pipeline's language against this list and copies the
# winner into the request, so advertising the regional tags is what keeps
# "zh-TW" from being flattened to a bare "zh" downstream. The server accepts
# either granularity.
_LOCALE_VARIANTS: dict[str, list[str]] = {
    "zh": ["zh-TW", "zh-CN", "zh-HK", "zh-Hant", "zh-Hans"],
    "en": ["en-US", "en-GB", "en-AU"],
}

# Preferred default per language, so a Taiwanese pipeline does not land on a
# mainland tag just because it sorts first.
_PREFERRED_DEFAULT = ("zh-TW", "zh", "en-US", "en")


def _expand_languages(base_codes: list[str]) -> list[str]:
    """Expand base language codes to the locale tags pipelines actually use."""
    expanded: list[str] = []
    for code in base_codes:
        expanded.append(code)
        expanded.extend(_LOCALE_VARIANTS.get(code, ()))
    return expanded


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: CortexTTSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one TTS entity per usable model."""
    client = config_entry.runtime_data.client
    async_setup_dynamic_models(
        hass,
        config_entry,
        async_add_entities,
        lambda model: [CortexTTSEntity(config_entry, client, model)],
    )


def _default_language(supported: list[str]) -> str:
    """Pick the language a pipeline gets when it names none."""
    for tag in _PREFERRED_DEFAULT:
        if tag in supported:
            return tag
    return supported[0] if supported else "zh"


class SpeakFields(TypedDict):
    """Everything a synthesis request carries beyond text, model and voice."""

    normalize_text: bool | None
    expand_numbers: bool | None
    convert_script: bool | None
    taiwan_readings: bool | None
    spoken_language: str | None
    instruct: str | None


def _explicit(options: dict[str, Any], key: str) -> bool | None:
    """An option as set, or ``None`` when it was not — never a default."""
    value = options.get(key)
    return None if value is None else bool(value)


class CortexTTSEntity(TextToSpeechEntity):
    """A voice backed by one Cortex TTS model."""

    # Not has_entity_name: the TTS component rejects an entity whose `name`
    # resolves to None ("TTS engine name is not set"), which is exactly what
    # naming the entity after its device produces. The model name is the
    # engine name users pick in a pipeline, so carry it explicitly.
    _attr_has_entity_name = False
    _attr_translation_key = "cortex_tts"

    def __init__(
        self,
        config_entry: CortexTTSConfigEntry,
        client: CortexTTSClient,
        model: ModelInfo,
    ) -> None:
        """Initialize the entity.

        Args:
            config_entry: Entry holding the server connection and runtime data.
            client: HTTP client for the Cortex TTS server.
            model: The model this entity speaks with.
        """
        self._config_entry = config_entry
        self._client = client
        self._model = model
        self._attr_unique_id = entity_unique_id(config_entry.entry_id, model.id)
        self._attr_name = model.name
        self._attr_supported_languages = _expand_languages(model.languages)
        self._attr_default_language = _default_language(self._attr_supported_languages)
        # Declared per model, not per integration: Home Assistant refuses an
        # option an entity has not declared, and offering one the server would
        # then reject with NO_LANGUAGE_CHOICE would move the error a step
        # further from the person who wrote the automation.
        self._attr_supported_options = [
            ATTR_VOICE,
            ATTR_AUDIO_OUTPUT,
            ATTR_PREFERRED_FORMAT,
            CONF_NORMALIZE_TEXT,
            CONF_EXPAND_NUMBERS,
            CONF_CONVERT_SCRIPT,
            CONF_TAIWAN_READINGS,
        ]
        if model.style_instruction:
            self._attr_supported_options.append(CONF_STYLE_INSTRUCTION)
        self._attr_device_info = device_for(config_entry.entry_id, model)

    @property
    def _voices(self) -> list[VoiceInfo]:
        runtime: CortexTTSRuntimeData = self._config_entry.runtime_data
        return runtime.voices.get(self._model.id, [])

    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return voices for a language.

        A voice that declares none is offered everywhere rather than hidden:
        it reads whatever it is given.

        Where the model has no voice in the language, what to do depends on
        whether it can be told one. A model whose voice decides the language
        has nothing to offer, and says so. One that takes a language
        parameter offers all of them instead: Qwen3-TTS reads ten languages
        with nine speakers, so German names no voice of its own and is still
        a sensible request — there the timbre and the language are separate
        things, and narrowing to nothing would leave the picker empty on a
        language the entity declares it supports.
        """

        def offer(voices: list[VoiceInfo]) -> list[Voice]:
            return [Voice(voice_id=v.id, name=v.name) for v in voices]

        # Both sides by primary subtag: a voice labelled zh-TW serves a
        # zh-CN pipeline too — the region says how its text is read, not
        # which pipelines may pick it.
        base = language.split("-")[0].lower()
        matching = [
            voice
            for voice in self._voices
            if voice.language is None or voice.language.split("-")[0].lower() == base
        ]
        if matching:
            return offer(matching)
        if self._model.language_choice and self._voices:
            return offer(self._voices)
        return None

    def async_supports_streaming_input(self) -> bool:
        """Every reply goes to the app over the socket, whatever the setting.

        Home Assistant's name for the hook is about *text* streaming in, and
        answering it `True` routes every reply through
        `async_stream_tts_audio` — including one handed over whole, which it
        wraps as a one-item stream. **Speaking mode** is then a question for
        the app rather than for the route: it travels in the opening frame,
        and `buffered` there means the app holds every byte to the end.

        Saying `False` for buffered would answer a different question: it
        would route those replies over HTTP, where the same text is cut the
        same way and the same figures come back under other names. The one
        thing that differed was loudness — a finished file is levelled and a
        stream is not, about 16 dB — so the setting would have changed the
        volume. The app levels a held reply the same way, and one route
        carries every reply.
        """
        return True

    def _stream_mode(self) -> str:
        """This model's configured mode, read fresh for every reply."""
        return stream_mode(self._config_entry, self._model)

    def _begin_speech(self) -> None:
        """Drop the previous reply's numbers before this one starts producing."""
        runtime: CortexTTSRuntimeData = self._config_entry.runtime_data
        for channel in runtime.sensors_by_model.get(self._model.id, ()):
            channel.handle_speech_start()

    def _push_stats(
        self, stats: SpeechStats, fields: frozenset[str] | None = None
    ) -> None:
        """Hand synthesis statistics to this model's diagnostic sensors."""
        runtime: CortexTTSRuntimeData = self._config_entry.runtime_data
        for channel in runtime.sensors_by_model.get(self._model.id, ()):
            channel.handle_speech(stats, fields)

    def _failed(
        self, err: Exception, language: str, translation_key: str
    ) -> HomeAssistantError:
        """Record a failed synthesis and return the error to raise."""
        self._push_stats(SpeechStats(success=False, language=language))
        if isinstance(err, CortexTTSAuthError):
            # The key was rotated under us; asking for a new one is the fix,
            # and every reply until then would fail the same way.
            self._config_entry.async_start_reauth(self.hass)
            translation_key = "invalid_api_key"
        _LOGGER.error("synthesis failed on %s: %s", self._model.id, err)
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={"error": str(err)},
        )

    def _delivery_options(
        self, language: str, options: dict[str, Any]
    ) -> dict[str, str | None]:
        """Return what to tell the model beyond the voice.

        The language is Home Assistant's own — the pipeline's, which is the
        language of the text. There is no separate option for it: a second
        one would have to mean something different from "what language is
        this", and nothing does. Sent to every model: the server prepares the
        text in that language on all of them, and tells the model too where
        the model takes one.

        Absent rather than empty when unset: the server reads "" as a request
        for something.
        """
        instruct = (
            options.get(CONF_STYLE_INSTRUCTION)
            if self._model.style_instruction
            else None
        )
        # Whole, not reduced to `zh`: how much of a tag means anything is the
        # model's to say, and one of them names Chinese dialects while another
        # names 646 languages. The server narrows it against the model's own
        # list; doing it here would decide for a model that may know better.
        return {
            "spoken_language": language or None,
            "instruct": str(instruct) if instruct else None,
        }

    def _speak_fields(self, language: str, options: dict[str, Any]) -> SpeakFields:
        """The four text switches and the two delivery fields, as one set.

        One structure rather than two dicts merged at the call site: their
        values are of different types, so merging them loses both — every
        keyword then reads as `bool | str | None`, and `normalize_text` will
        take a string as far as the checker knows.
        """
        text = self._text_options(options)
        delivery = self._delivery_options(language, options)
        return SpeakFields(
            normalize_text=text["normalize_text"],
            expand_numbers=text["expand_numbers"],
            convert_script=text["convert_script"],
            taiwan_readings=text["taiwan_readings"],
            spoken_language=delivery["spoken_language"],
            instruct=delivery["instruct"],
        )

    def _text_options(self, options: dict[str, Any]) -> dict[str, bool | None]:
        """Return the text-pipeline switches for a call.

        Each switch travels only when an automation set it outright. Left
        out, the server answers from its own settings — a rule per model and
        language — and then from what it knows: normalisation on for every
        language (no model here is trusted with a unit symbol or a date), a
        bare number expanded only for a model that cannot say a digit at all,
        the two Chinese rewrites decided from the language.
        """
        return {
            "normalize_text": _explicit(options, CONF_NORMALIZE_TEXT),
            "expand_numbers": _explicit(options, CONF_EXPAND_NUMBERS),
            "convert_script": _explicit(options, CONF_CONVERT_SCRIPT),
            "taiwan_readings": _explicit(options, CONF_TAIWAN_READINGS),
        }

    async def async_stream_tts_audio(
        self, request: TTSAudioRequest
    ) -> TTSAudioResponse:
        """Speak a reply while it is still being written.

        MP3, so there is no length to declare before the audio exists. See
        `STREAM_FORMAT`.
        """
        return TTSAudioResponse(STREAM_FORMAT, self._stream_live(request))

    async def _stream_live(self, request: TTSAudioRequest) -> AsyncGenerator[bytes]:
        """Forward the reply to the app as it is written; play what comes back.

        The app decides when to render what, how much to hold before the
        first sound, and whether to stream at all — it holds the measurements
        of its own host. What is decided here is only what this side can
        know: the words, as they arrive, and that the listener is still there.
        Leaving this generator early — Home Assistant closing it — sends the
        app `cancel`, so the model stops rendering for nobody.
        """
        fields = self._speak_fields(request.language, request.options)
        voice = request.options.get(ATTR_VOICE)
        self._begin_speech()
        started = time.perf_counter()
        characters = 0
        spoken: list[str] = []
        first_audio_ms = 0.0
        heard = False
        planned: str | None = None
        done: dict[str, Any] | None = None

        async def forward(session: LiveSession) -> None:
            nonlocal characters
            async for chunk in request.message_gen:
                characters += len(chunk)
                spoken.append(chunk)
                await session.send_text(chunk)
            await session.end()
            # The words are known now; the audio may be a long way off.
            self._push_stats(
                SpeechStats(
                    success=True,
                    characters=characters,
                    text="".join(spoken),
                    language=request.language,
                    voice=str(voice or ""),
                ),
                TEXT_FIELDS,
            )

        try:
            async with self._client.speak_live(
                model=self._model.id,
                voice=voice,
                mode=self._stream_mode(),
                **fields,
            ) as session:
                forwarder = asyncio.create_task(forward(session))
                try:
                    async for kind, payload in session.frames():
                        if kind == "batch":
                            # The plan in force, before any audio: shown at
                            # the first frame and corrected by `done` — a
                            # reply that turns out to fit one request is
                            # spoken whole whatever was planned.
                            planned = str(payload.get("mode") or "") or None
                        elif kind == "audio":
                            if not heard:
                                heard = True
                                # Measured at the first byte that arrives: the
                                # app's opening hold is part of what the
                                # listener waits, and it is over by now.
                                first_audio_ms = (time.perf_counter() - started) * 1000
                                self._push_stats(
                                    SpeechStats(
                                        success=True,
                                        first_audio_ms=round(first_audio_ms, 1),
                                        mode=planned or STREAM_WHOLE,
                                        language=request.language,
                                        voice=str(voice or ""),
                                    ),
                                    FIRST_AUDIO_FIELDS
                                    if planned
                                    else FIRST_AUDIO_FIELDS - {"mode"},
                                )
                            yield payload
                        elif kind == "done":
                            done = payload
                finally:
                    # A forwarder still running means the reply ended before
                    # the writer did — the listener left, or the app failed.
                    if not forwarder.done():
                        forwarder.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await forwarder
        except CortexTTSError as err:
            raise self._failed(err, request.language, "synthesis_failed") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise self._failed(err, request.language, "cannot_connect") from err

        if done is None or not done.get("audio_seconds"):
            # Nothing was synthesised — an empty reply, or one that was all
            # punctuation — or the session ended without saying how it went.
            _LOGGER.debug("nothing to speak on %s", self._model.id)
            return

        # The app's figures, not this side's clock: this side's wall time
        # spans the writer and the opening hold, which is a wait, not a cost.
        audio_seconds = float(done.get("audio_seconds", 0.0))
        render_ms = float(done.get("render_ms", 0.0))
        writer_ms = done.get("writer_ms")
        load_ms = float(done.get("load_ms", 0.0))
        rtf = done.get("rtf")
        margin = done.get("min_lead_s")
        mode = str(done.get("mode", STREAM_WHOLE))
        batches = int(done.get("batches", 0))
        self._push_stats(
            SpeechStats(
                success=True,
                characters=characters,
                text="".join(spoken),
                audio_seconds=round(audio_seconds, 3),
                inference_ms=round(render_ms, 1),
                rtf=float(rtf) if rtf is not None else 0.0,
                first_audio_ms=round(first_audio_ms, 1),
                load_ms=load_ms,
                writer_ms=float(writer_ms) if writer_ms is not None else None,
                language=request.language,
                voice=str(voice or ""),
                mode=mode,
                batches=batches,
                margin_seconds=None if margin is None else float(margin),
            )
        )
        _LOGGER.debug(
            "%s %d chars on %s in %d batch(es) as %.2fs of audio, rendered in "
            "%.0fms (RTF %s, first %.0fms, margin %+.2fs)",
            mode,
            characters,
            self._model.id,
            batches,
            audio_seconds,
            render_ms,
            rtf,
            first_audio_ms,
            float(margin) if margin is not None else 0.0,
        )
