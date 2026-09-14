"""TTS platform for Cortex TTS — one entity per usable model."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any, TypedDict

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

from .client import CortexTTSAuthError, CortexTTSClient, CortexTTSError
from .const import (
    ASSUMED_DEFICIT,
    CONF_CONVERT_SCRIPT,
    CONF_EXPAND_NUMBERS,
    CONF_NORMALIZE_TEXT,
    CONF_STYLE_INSTRUCTION,
    CONF_TAIWAN_READINGS,
    DOMAIN,
    FIRST_AUDIO_FIELDS,
    MAX_REQUEST_SECONDS,
    STREAM_BUFFERED,
    STREAM_COALESCED,
    STREAM_FORMAT,
)
from .entity import device_for
from .entity_setup import async_setup_dynamic_models
from .models import (
    CortexTTSRuntimeData,
    ModelInfo,
    SpeechStats,
    VoiceInfo,
    entity_unique_id,
    head_start,
    stream_mode,
)
from .text import audio_seconds, deliverable

if TYPE_CHECKING:
    from . import CortexTTSConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Home Assistant asks for mp3 unless told otherwise; answering in another
# container makes it transcode every reply through ffmpeg.
SUPPORTED_FORMATS = ("mp3", "wav", "flac", "ogg")

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


def _rtf(elapsed_ms: float, audio_seconds: float) -> float:
    """Return inference time over audio length; 0.0 when there is no audio."""
    if not audio_seconds:
        return 0.0
    return round(elapsed_ms / 1000 / audio_seconds, 3)


class _HeadStart:
    """Banks the opening seconds of a stream before any of it is sent.

    A model that renders slower than its audio plays loses ground for the
    whole reply and never wins it back — measured on MOSS-TTS-Nano at 1.045x,
    a forty-second reply leaves the player 1.8 s short. Nothing downstream can
    repair that, because the audio genuinely is not ready yet. What can be
    changed is *when* the player starts: hand it the difference up front and
    it has the rest of the reply to spend it.

    The WAV header is banked too, so a player cannot start on a header whose
    audio is still seconds away.

    The bank is sized for the longest reply, and most replies are shorter.
    `shorten_for` is how a reply that is plainly too short to need one gets
    out of paying for it.
    """

    __slots__ = ("_remaining", "_held", "banked")

    def __init__(self, seconds: float) -> None:
        self._remaining = seconds
        self.banked = 0.0
        """Seconds of audio held back, which is what the opening release carries."""
        self._held: list[bytes] = []

    @property
    def is_open(self) -> bool:
        """Whether audio now goes straight out."""
        return self._remaining <= 0.0

    def shorten_for(self, seconds: float) -> None:
        """Lower the bank to what a reply this long can lose.

        Called once the whole reply is known, which is most of the time: a
        caller that hands over a finished message, or a writer that finished
        before synthesis caught up. Never raises the bank — a reply longer
        than expected keeps what was configured.
        """
        needed = ASSUMED_DEFICIT * seconds
        self._remaining = min(self._remaining, max(0.0, needed - self.banked))

    def feed(self, chunk: bytes, seconds: float) -> list[bytes]:
        """Return what may be sent now, which is nothing until the bank fills."""
        if self._remaining <= 0.0:
            return [chunk]
        self._held.append(chunk)
        self.banked += seconds
        self._remaining -= seconds
        if self._remaining > 0.0:
            return []
        return self.flush()

    def flush(self) -> list[bytes]:
        """Release whatever is held. A reply shorter than the bank ends here."""
        held, self._held = self._held, []
        self._remaining = 0.0
        return held


class _Delivery:
    """Watches a stream go out, so "was it smooth" stops being an opinion.

    `margin` is the least audio a listener still held, in seconds: everything
    sent before a chunk, minus everything played since the first chunk
    started the reply. It opens at the banked head start and falls whenever
    rendering runs slower than playback. Negative means the listener ran dry
    — the samples did not exist yet, and no downstream buffer could have
    covered it.

    The arriving chunk is not counted as held, because audio arriving now
    cannot fill a silence that has already been heard.
    """

    __slots__ = ("_started", "_audio", "margin")

    def __init__(self) -> None:
        self._started: float | None = None
        self._audio = 0.0
        self.margin: float | None = None
        """Unmeasured until a second chunk exists to arrive late."""

    def sent(self, seconds: float) -> None:
        """Record that this much audio has now left."""
        now = time.perf_counter()
        if self._started is None:
            # Playback starts here, so nothing has been consumed yet.
            self._started = now
        else:
            lead = self._audio - (now - self._started)
            self.margin = lead if self.margin is None else min(self.margin, lead)
        self._audio += seconds


async def _coalesced(
    sentences: AsyncIterator[str],
) -> AsyncGenerator[tuple[str, float]]:
    """Group sentences into requests of a size the model renders well.

    There is a cliff on either side of this. One sentence per request makes
    the server pay a fresh prefill for every sentence — on MOSS-TTS-Nano that
    is 0.37 s of dead air per boundary, measured at 41% behind playback for a
    reply of short sentences. One request for the whole reply removes those,
    but the model attends over everything it has generated so far, so the bill
    grows with the square of the request: 17.8% behind for a 55-second story
    in one go, against 6.5% for the same story in two.

    So a batch takes everything that arrived while the last request was
    streaming, up to `MAX_REQUEST_SECONDS` of audio, and leaves the rest for
    the next one. A slow writer still degrades to one sentence per request,
    which is all there is to send.

    Yields each batch with the length of the whole reply once the writer has
    finished, which is what lets a head start be sized against a length that
    is known rather than guessed. Zero while the reply is still being written.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def read_ahead() -> None:
        try:
            async for sentence in sentences:
                queue.put_nowait(sentence)
        finally:
            queue.put_nowait(None)

    reader = asyncio.create_task(read_ahead())
    pending: list[str] = []
    finished = False
    try:
        while True:
            if not pending:
                first = await queue.get()
                if first is None:
                    break
                pending.append(first)
            # Everything already waiting arrived while the last request was
            # streaming; taking it now is what removes that request's boundary.
            while not queue.empty():
                nxt = queue.get_nowait()
                if nxt is None:
                    finished = True
                    break
                pending.append(nxt)

            reply_seconds = (
                sum(audio_seconds(part) for part in pending) if finished else 0.0
            )

            batch: list[str] = []
            size = 0.0
            # Always take one: every piece arrives already within the limit,
            # so the only way one can exceed it is text this measurement has
            # never seen, and it still has to be spoken.
            while pending and (
                not batch or size + audio_seconds(pending[0]) <= MAX_REQUEST_SECONDS
            ):
                size += audio_seconds(pending[0])
                batch.append(pending.pop(0))
            yield "".join(batch), reply_seconds

            if finished and not pending:
                break
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader


class SpeakFields(TypedDict):
    """Everything a synthesis request carries beyond text, model and voice."""

    normalize_text: bool
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

        base = language.split("-")[0].lower()
        matching = [
            voice
            for voice in self._voices
            if voice.language is None or voice.language.lower() == base
        ]
        if matching:
            return offer(matching)
        if self._model.language_choice and self._voices:
            return offer(self._voices)
        return None

    def async_supports_streaming_input(self) -> bool:
        """Return whether this model should speak before the reply is finished.

        Home Assistant's name for the hook is about *text* streaming in; what
        it decides here is whether audio goes out before the text is complete.
        The answer is this model's own setting, in its own subentry, buffered
        until someone changes it. It routes every reply, including one handed
        over whole: Home Assistant wraps a finished message in a one-item
        stream when this returns true.
        """
        return self._stream_mode() != STREAM_BUFFERED

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

        Normalisation reads the fixed shapes a sensor produces — a unit, a
        clock, a date — in the language's own words, so it belongs on for
        every language: the model pronounces no Arabic numeral at all. A bare
        number (a room, a phone, a model) is the server's default not to
        read, and the two Chinese rewrites are its to decide from the
        language — so those travel only when an automation set them
        outright, for a caller who knows what its numbers are.
        """
        return {
            "normalize_text": bool(options.get(CONF_NORMALIZE_TEXT, True)),
            "expand_numbers": _explicit(options, CONF_EXPAND_NUMBERS),
            "convert_script": _explicit(options, CONF_CONVERT_SCRIPT),
            "taiwan_readings": _explicit(options, CONF_TAIWAN_READINGS),
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
                **self._speak_fields(language, options),
            )
        except CortexTTSError as err:
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
                mode=STREAM_BUFFERED,
                requests=1,
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

        MP3, so there is no length to declare before the audio exists and no
        container to reopen at each request boundary — the frames of one
        request follow the frames of the last and any decoder carries on. See
        `STREAM_FORMAT`.
        """
        return TTSAudioResponse(STREAM_FORMAT, self._stream_sentences(request))

    async def _stream_sentences(
        self, request: TTSAudioRequest
    ) -> AsyncGenerator[bytes]:
        """Synthesise the reply as it is written, as one continuous stream.

        What goes into each request is the mode's business: one sentence, or
        everything that arrived while the last request was still streaming.
        """
        detector = SentenceBoundaryDetector()
        fields = self._speak_fields(request.language, request.options)
        voice = request.options.get(ATTR_VOICE)
        produced = False
        characters = 0
        audio_seconds = 0.0
        sent = 0
        self._begin_speech()
        started = time.perf_counter()
        first_audio_ms = 0.0

        async def _sentences() -> AsyncGenerator[str]:
            """Yield speakable pieces as the reply is written.

            A sentence longer than a request may be is cut here rather than
            in either mode, so both of them inherit the same limit: the
            run-on sentence is the one shape that neither mode could deliver
            in time, because it arrives as a single lump whatever is done
            with it afterwards.
            """
            async for chunk in request.message_gen:
                for sentence in detector.add_chunk(chunk):
                    for piece in deliverable(sentence, MAX_REQUEST_SECONDS):
                        yield piece
            if tail := detector.finish():
                for piece in deliverable(tail, MAX_REQUEST_SECONDS):
                    yield piece

        mode = self._stream_mode()
        bank = _HeadStart(head_start(self._config_entry, self._model))
        delivery = _Delivery()
        sentences = _sentences()
        requests = (
            _coalesced(sentences)
            if mode == STREAM_COALESCED
            # One sentence per request never knows how much more is coming,
            # so it can never shorten the bank.
            else ((sentence, 0) async for sentence in sentences)
        )

        async for text, reply_seconds in requests:
            if not text.strip():
                continue
            if sent == 0 and reply_seconds:
                # The writer had finished before the first request went out,
                # so how much this reply can lose is arithmetic rather than a
                # guess — even when it takes several requests to say it.
                bank.shorten_for(reply_seconds)
            # How many requests a reply took is the whole difference between
            # the streaming modes, and nothing else records it.
            sent += 1

            # The server streams within a request too, so the first audio
            # arrives partway through the first sentence rather than at the end
            # of it. A model that cannot do that sends the whole request as one
            # chunk.
            try:
                async for bitrate, frames in self._client.speak_stream(
                    text,
                    model=self._model.id,
                    voice=voice,
                    **fields,
                ):
                    produced = True
                    # A constant bitrate is what turns a byte count into a
                    # duration; nothing here decodes the audio.
                    seconds = len(frames) * 8 / bitrate
                    audio_seconds += seconds
                    was_open = bank.is_open
                    ready = bank.feed(frames, seconds)
                    if ready and not first_audio_ms:
                        # Measured at the first byte that leaves rather than
                        # the first that arrives: a banked head start is part
                        # of what the listener waits.
                        first_audio_ms = (time.perf_counter() - started) * 1000
                        # Reporting it here rather than with the totals is the
                        # difference between seeing it while the reply is
                        # still playing and seeing it a reply later.
                        self._push_stats(
                            SpeechStats(
                                success=True,
                                first_audio_ms=round(first_audio_ms, 1),
                                language=request.language,
                                voice=str(voice or ""),
                                mode=mode,
                            ),
                            FIRST_AUDIO_FIELDS,
                        )
                    if ready:
                        # The opening release carries everything banked; every
                        # one after it carries just this chunk.
                        delivery.sent(seconds if was_open else bank.banked)
                    for chunk in ready:
                        yield chunk
            except CortexTTSError as err:
                if err.code == "EMPTY_TEXT":
                    # Nothing survived the text pipeline — a line of bare
                    # punctuation. The rest of the reply is still worth saying.
                    continue
                raise self._failed(err, request.language, "synthesis_failed") from err
            except (aiohttp.ClientError, TimeoutError) as err:
                raise self._failed(err, request.language, "cannot_connect") from err

            characters += len(text)

        if remainder := bank.flush():
            # The reply was shorter than the bank, so it never released.
            if not first_audio_ms:
                first_audio_ms = (time.perf_counter() - started) * 1000
            for chunk in remainder:
                yield chunk

        if not produced:
            # Nothing was synthesised — an empty reply, or one that was all
            # punctuation.
            _LOGGER.debug("nothing to speak on %s", self._model.id)
            return

        # Reported as generation time, not inference time: see models.py.
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._push_stats(
            SpeechStats(
                success=True,
                characters=characters,
                audio_seconds=round(audio_seconds, 3),
                generation_ms=round(elapsed_ms, 1),
                rtf=_rtf(elapsed_ms, audio_seconds),
                first_audio_ms=round(first_audio_ms, 1),
                language=request.language,
                voice=str(voice or ""),
                mode=mode,
                requests=sent,
                margin_seconds=delivery.margin,
            )
        )
        _LOGGER.debug(
            "%s %d chars on %s in %d request(s) as %.2fs of audio in %.0fms "
            "(RTF %.2f, first %.0fms, margin %+.2fs)",
            mode,
            characters,
            self._model.id,
            sent,
            audio_seconds,
            elapsed_ms,
            _rtf(elapsed_ms, audio_seconds),
            first_audio_ms,
            delivery.margin if delivery.margin is not None else 0.0,
        )
