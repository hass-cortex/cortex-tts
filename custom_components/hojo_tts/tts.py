"""TTS platform for Hojo TTS — one entity per usable model."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.components.tts import (
    ATTR_AUDIO_OUTPUT,
    ATTR_PREFERRED_FORMAT,
    ATTR_VOICE,
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
    TtsAudioType,
    Voice,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from sentence_stream import SentenceBoundaryDetector

from .audio import PcmFormat, duration_seconds, split_wav, stream_header
from .client import HojoTTSClient, HojoTTSError
from .const import (
    CONF_CONVERT_SCRIPT,
    CONF_NORMALIZE_TEXT,
    CONF_STREAM_MODELS,
    DOMAIN,
    FIRST_AUDIO_FIELDS,
)
from .entity import device_for
from .entity_setup import async_setup_dynamic_models
from .models import HojoTTSRuntimeData, ModelInfo, SpeechStats, VoiceInfo

if TYPE_CHECKING:
    from . import HojoTTSConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

SUPPORTED_FORMATS = ("wav", "flac", "ogg")

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
    config_entry: HojoTTSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one TTS entity per usable model."""
    client = config_entry.runtime_data.client
    async_setup_dynamic_models(
        hass,
        config_entry,
        async_add_entities,
        lambda model: [HojoTTSEntity(config_entry, client, model)],
    )


def _default_language(supported: list[str]) -> str:
    """Pick the language a pipeline gets when it names none."""
    for tag in _PREFERRED_DEFAULT:
        if tag in supported:
            return tag
    return supported[0] if supported else "zh"


def _rtf(inference_ms: float, audio_seconds: float) -> float:
    """Return inference time over audio length; 0.0 when there is no audio."""
    if not audio_seconds:
        return 0.0
    return round(inference_ms / 1000 / audio_seconds, 3)


class HojoTTSEntity(TextToSpeechEntity):
    """A voice backed by one Hojo TTS model."""

    # Not has_entity_name: the TTS component rejects an entity whose `name`
    # resolves to None ("TTS engine name is not set"), which is exactly what
    # naming the entity after its device produces. The model name is the
    # engine name users pick in a pipeline, so carry it explicitly.
    _attr_has_entity_name = False

    def __init__(
        self,
        config_entry: HojoTTSConfigEntry,
        client: HojoTTSClient,
        model: ModelInfo,
    ) -> None:
        """Initialize the entity.

        Args:
            config_entry: Entry holding the server connection and runtime data.
            client: HTTP client for the Hojo TTS server.
            model: The model this entity speaks with.
        """
        self._config_entry = config_entry
        self._client = client
        self._model = model
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{model.id}"
        self._attr_name = model.name
        self._attr_supported_languages = _expand_languages(model.languages)
        self._attr_default_language = _default_language(self._attr_supported_languages)
        self._attr_supported_options = [
            ATTR_VOICE,
            ATTR_AUDIO_OUTPUT,
            ATTR_PREFERRED_FORMAT,
            CONF_NORMALIZE_TEXT,
            CONF_CONVERT_SCRIPT,
        ]
        self._attr_device_info = device_for(config_entry.entry_id, model)

    @property
    def _voices(self) -> list[VoiceInfo]:
        runtime: HojoTTSRuntimeData = self._config_entry.runtime_data
        return runtime.voices.get(self._model.id, [])

    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return voices for a language.

        Cloned voices carry no declared language, so they are offered for every
        language rather than hidden — the reference recording, not a metadata
        field, decides what they can say.
        """
        base = language.split("-")[0].lower()
        matching = [
            Voice(voice_id=voice.id, name=voice.name)
            for voice in self._voices
            if voice.language is None or voice.language.lower() == base
        ]
        return matching or None

    def async_supports_streaming_input(self) -> bool:
        """Return whether this model should be spoken sentence by sentence.

        Home Assistant's name for the hook is about *text* streaming in; what
        it decides here is whether audio goes out per sentence. Chosen per
        model — until a choice is saved, one fast enough to stay ahead of the
        speaker does, a slower one does not.
        """
        chosen = self._config_entry.options.get(CONF_STREAM_MODELS)
        if chosen is None:
            return self._model.outruns_playback
        return self._model.id in chosen

    def _begin_speech(self) -> None:
        """Drop the previous reply's numbers before this one starts producing."""
        runtime: HojoTTSRuntimeData = self._config_entry.runtime_data
        for channel in runtime.sensors_by_model.get(self._model.id, ()):
            channel.handle_speech_start()

    def _push_stats(
        self, stats: SpeechStats, fields: frozenset[str] | None = None
    ) -> None:
        """Hand synthesis statistics to this model's diagnostic sensors."""
        runtime: HojoTTSRuntimeData = self._config_entry.runtime_data
        for channel in runtime.sensors_by_model.get(self._model.id, ()):
            channel.handle_speech(stats, fields)

    def _failed(
        self, err: Exception, language: str, translation_key: str
    ) -> HomeAssistantError:
        """Record a failed synthesis and return the error to raise."""
        self._push_stats(SpeechStats(success=False, language=language))
        _LOGGER.error("synthesis failed on %s: %s", self._model.id, err)
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={"error": str(err)},
        )

    def _text_options(self, language: str, options: dict[str, Any]) -> dict[str, bool]:
        """Return the two text-pipeline switches for a call.

        Normalisation spells numbers in the script of the text, so it belongs
        on for every language: the model pronounces no Arabic numeral at all,
        and an unexpanded digit is silent rather than merely wrong. Script
        conversion rewrites glyphs into Simplified and is meaningless outside
        Chinese, so it follows the language tag. An explicit option still wins,
        for a caller whose text is already prepared.
        """
        chinese = language.lower().startswith("zh")
        return {
            "normalize_text": bool(options.get(CONF_NORMALIZE_TEXT, True)),
            "convert_script": bool(options.get(CONF_CONVERT_SCRIPT, chinese)),
        }

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        """Synthesise a message.

        Args:
            message: Text to speak.
            language: Language tag chosen by the pipeline.
            options: Per-call options; ``voice`` selects a voice and the two
                text switches allow a caller whose text is already prepared to
                skip the conversion passes.

        Returns:
            The audio extension and bytes.

        Raises:
            HomeAssistantError: The server could not synthesise the message.
        """
        requested = options.get(ATTR_PREFERRED_FORMAT) or options.get(ATTR_AUDIO_OUTPUT)
        audio_format = requested if requested in SUPPORTED_FORMATS else "wav"

        self._begin_speech()
        started = time.perf_counter()
        try:
            audio, stats = await self._client.speak(
                message,
                model=self._model.id,
                voice=options.get(ATTR_VOICE),
                audio_format=audio_format,
                **self._text_options(language, options),
            )
        except HojoTTSError as err:
            raise self._failed(err, language, "synthesis_failed") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise self._failed(err, language, "cannot_connect") from err

        self._push_stats(
            SpeechStats(
                success=True,
                characters=len(message),
                audio_seconds=stats.get("audio_seconds", 0.0),
                inference_ms=stats.get("inference_ms", 0.0),
                rtf=stats.get("rtf", 0.0),
                # Nothing can play before the one call returns, so the whole
                # round trip is the wait. Kept comparable with the streamed
                # path on purpose: the difference is what streaming buys.
                first_audio_ms=(time.perf_counter() - started) * 1000,
                language=language,
                voice=str(options.get(ATTR_VOICE) or ""),
            )
        )
        _LOGGER.debug(
            "spoke %d chars on %s in %.0fms (RTF %.2f)",
            len(message),
            self._model.id,
            stats.get("inference_ms", 0.0),
            stats.get("rtf", 0.0),
        )
        return audio_format, audio

    async def async_stream_tts_audio(
        self, request: TTSAudioRequest
    ) -> TTSAudioResponse:
        """Speak a reply while it is still being written.

        Always WAV: the sentences are concatenated as raw frames, which no
        compressed container allows. Home Assistant transcodes afterwards when
        the consumer asked for something else.
        """
        return TTSAudioResponse("wav", self._stream_sentences(request))

    async def _stream_sentences(
        self, request: TTSAudioRequest
    ) -> AsyncGenerator[bytes]:
        """Synthesise each finished sentence and emit one continuous WAV stream."""
        detector = SentenceBoundaryDetector()
        text_options = self._text_options(request.language, request.options)
        voice = request.options.get(ATTR_VOICE)
        pcm_format: PcmFormat | None = None
        characters = 0
        audio_seconds = 0.0
        inference_ms = 0.0
        # Summed inference says what the model cost, never what the listener
        # waited: sentence one is already playing while the rest is generated.
        self._begin_speech()
        started = time.perf_counter()
        first_audio_ms = 0.0

        async def _sentences() -> AsyncGenerator[str]:
            """Yield complete sentences as the reply is written."""
            async for chunk in request.message_gen:
                for sentence in detector.add_chunk(chunk):
                    yield sentence
            if tail := detector.finish():
                yield tail

        async for sentence in _sentences():
            if not sentence.strip():
                continue
            try:
                audio, stats = await self._client.speak(
                    sentence,
                    model=self._model.id,
                    voice=voice,
                    audio_format="wav",
                    **text_options,
                )
            except HojoTTSError as err:
                if err.code == "EMPTY_TEXT":
                    # Nothing survived the text pipeline — a line of bare
                    # punctuation. The rest of the reply is still worth saying.
                    continue
                raise self._failed(err, request.language, "synthesis_failed") from err
            except (aiohttp.ClientError, TimeoutError) as err:
                raise self._failed(err, request.language, "cannot_connect") from err

            fmt, frames = split_wav(audio)
            if pcm_format is None:
                pcm_format = fmt
                yield stream_header(fmt)
            elif fmt != pcm_format:
                raise self._failed(
                    ValueError(f"sample format changed from {pcm_format} to {fmt}"),
                    request.language,
                    "synthesis_failed",
                )

            characters += len(sentence)
            audio_seconds += duration_seconds(fmt, frames)
            inference_ms += stats.get("inference_ms", 0.0)
            if not first_audio_ms:
                first_audio_ms = (time.perf_counter() - started) * 1000
                # The wait is settled now. Reporting it here rather than with
                # the totals is the difference between seeing it while the
                # reply is still playing and seeing it a reply later.
                self._push_stats(
                    SpeechStats(
                        success=True,
                        first_audio_ms=round(first_audio_ms, 1),
                        language=request.language,
                        voice=str(voice or ""),
                        streamed=True,
                    ),
                    FIRST_AUDIO_FIELDS,
                )
            yield frames

        if pcm_format is None:
            # Nothing was synthesised — an empty reply, or one that was all
            # punctuation. Emitting a bare header would claim audio there is
            # none, so the stream ends empty.
            _LOGGER.debug("nothing to speak on %s", self._model.id)
            return

        self._push_stats(
            SpeechStats(
                success=True,
                characters=characters,
                audio_seconds=round(audio_seconds, 3),
                inference_ms=round(inference_ms, 1),
                rtf=_rtf(inference_ms, audio_seconds),
                first_audio_ms=round(first_audio_ms, 1),
                language=request.language,
                voice=str(voice or ""),
                streamed=True,
            )
        )
        _LOGGER.debug(
            "streamed %d chars on %s in %.0fms (RTF %.2f)",
            characters,
            self._model.id,
            inference_ms,
            inference_ms / 1000 / audio_seconds if audio_seconds else 0.0,
        )
