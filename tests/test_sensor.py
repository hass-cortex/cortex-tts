"""The diagnostic sensors: what each reads, and when it is cleared."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.cortex_tts.const import (
    FIRST_AUDIO_FIELDS,
    SPOKEN_MODES,
)
from custom_components.cortex_tts.models import SpeechStats
from custom_components.cortex_tts.sensor import DESCRIPTIONS, CortexTTSSensor

_BY_KEY = {d.key: d for d in DESCRIPTIONS}


def _stats(**overrides: object) -> SpeechStats:
    base = {
        "success": True,
        "characters": 20,
        "audio_seconds": 4.22,
        "inference_ms": 2283.0,
        "rtf": 0.541,
        "first_audio_ms": 2289.4,
        "language": "zh-TW",
        "voice": "hojo_zh_f_01",
        "mode": "streaming",
    }
    return SpeechStats(**{**base, **overrides})  # type: ignore[arg-type]


class TestDescriptions:
    def test_every_duration_declares_its_device_class(self) -> None:
        # A unit without the class leaves Home Assistant unable to convert or
        # display it.
        for key in ("inference_ms", "first_audio_ms", "audio_seconds"):
            description = _BY_KEY[key]
            assert description.native_unit_of_measurement is not None
            assert description.device_class is SensorDeviceClass.DURATION

    def test_nothing_is_cumulative(self) -> None:
        # Every sensor describes the last reply, so a total would mix replies.
        assert not any(
            d.state_class is SensorStateClass.TOTAL_INCREASING for d in DESCRIPTIONS
        )

    def test_first_audio_fields_name_real_sensors(self) -> None:
        assert set(_BY_KEY) >= FIRST_AUDIO_FIELDS

    def test_every_sensor_is_translated(self) -> None:
        for description in DESCRIPTIONS:
            assert description.translation_key == description.key


class TestValueFn:
    def test_each_reads_its_own_field(self) -> None:
        stats = _stats()
        assert _BY_KEY["inference_ms"].value_fn(stats) == 2283.0
        assert _BY_KEY["audio_seconds"].value_fn(stats) == 4.22
        assert _BY_KEY["rtf"].value_fn(stats) == 0.541
        assert _BY_KEY["characters"].value_fn(stats) == 20

    def test_the_mode_reads_as_the_word_the_option_list_declares(self) -> None:
        """An enum sensor whose state is not in `options` logs and shows unknown."""
        description = _BY_KEY["mode"]
        assert description.options is not None
        for mode in SPOKEN_MODES:
            assert description.value_fn(_stats(mode=mode)) == mode
            assert mode in description.options

    def test_the_sensor_reports_what_was_spoken_not_what_was_set(self) -> None:
        """`auto` is a setting and never an outcome: it asks the app to choose.

        `buffered` is both — a thing to ask for, and a thing the app reports
        having done — so the two tuples do overlap there.
        """
        assert set(_BY_KEY["mode"].options or ()) == set(SPOKEN_MODES)
        assert "auto" not in (_BY_KEY["mode"].options or ())
        assert "buffered" in SPOKEN_MODES

    def test_every_word_either_frame_can_send_is_an_option(self) -> None:
        """The sensor is written twice per reply, from two vocabularies.

        A `batch` frame names the verdict in force before any audio exists
        (`streaming` or `buffered`); `done` repeats what happened. A value
        outside `options` does not mislabel the reply — Core raises, and the
        exception comes back as a 500 from `/api/tts_proxy`, so nothing plays
        at all.
        """
        batch_frame = {"streaming", "buffered"}
        done_frame = {"streaming", "buffered"}
        assert batch_frame | done_frame <= set(_BY_KEY["mode"].options or ())


class TestClearing:
    @staticmethod
    def _sensor(key: str) -> CortexTTSSensor:
        sensor = object.__new__(CortexTTSSensor)
        sensor.entity_description = _BY_KEY[key]
        sensor._attr_native_value = None
        sensor.hass = None
        return sensor

    def test_a_new_reply_clears_the_previous_number(self) -> None:
        sensor = self._sensor("inference_ms")
        sensor.handle_speech(_stats())
        assert sensor._attr_native_value == 2283.0
        sensor.handle_speech_start()
        assert sensor._attr_native_value is None

    def test_a_failure_writes_nothing(self) -> None:
        # Cleared at the start and left unknown: zero would read as "instant".
        sensor = self._sensor("inference_ms")
        sensor.handle_speech_start()
        sensor.handle_speech(SpeechStats(success=False, language="zh-TW"))
        assert sensor._attr_native_value is None

    def test_a_partial_push_touches_only_its_own_fields(self) -> None:
        early = self._sensor("first_audio_ms")
        late = self._sensor("inference_ms")
        for sensor in (early, late):
            sensor.handle_speech(_stats(), FIRST_AUDIO_FIELDS)
        assert early._attr_native_value == 2289.4
        assert late._attr_native_value is None


class TestUnmeasuredIsNotZero:
    """A field that was never filled in must not read as a measurement.

    Reported from production: `sensor.moss_tts_nano_real_time_factor` sat at 0
    after every streamed reply. Zero is not a possible real-time factor — a
    synthesis that produced audio took time — so showing it as a number made a
    gap in the plumbing look like an impossibly fast model.
    """

    @pytest.mark.parametrize(
        "key", ["inference_ms", "rtf", "audio_seconds", "first_audio_ms"]
    )
    def test_a_zero_measurement_reads_as_unknown(self, key: str) -> None:
        assert _BY_KEY[key].value_fn(SpeechStats(success=True)) is None

    @pytest.mark.parametrize(
        ("key", "field"),
        [
            ("inference_ms", "inference_ms"),
            ("rtf", "rtf"),
            ("audio_seconds", "audio_seconds"),
            ("first_audio_ms", "first_audio_ms"),
        ],
    )
    def test_a_real_measurement_still_reads(self, key: str, field: str) -> None:
        stats = SpeechStats(success=True, **{field: 1.5})
        assert _BY_KEY[key].value_fn(stats) == pytest.approx(1.5)

    def test_counts_and_words_are_untouched(self) -> None:
        """Zero characters is a real answer; the rule is only for durations."""
        assert _BY_KEY["characters"].value_fn(SpeechStats(success=True)) == 0

    def test_no_request_at_all_is_not_a_request(self) -> None:
        """Unlike characters, zero batches cannot have produced a reply."""
        assert _BY_KEY["requests"].value_fn(SpeechStats(success=True)) is None


class TestRequests:
    """How many times the app asked the model, which the mode alone cannot say.

    Measured over 308 production voice replies, 70% were a single sentence.
    Each of those is one request whichever way the reply was spoken.
    """

    def test_a_single_piece_reply_says_one(self) -> None:
        stats = _stats(mode="buffered", batches=1)
        assert _BY_KEY["requests"].value_fn(stats) == 1
        assert _BY_KEY["mode"].value_fn(stats) == "buffered"

    def test_a_grouped_reply_says_how_many(self) -> None:
        assert _BY_KEY["requests"].value_fn(_stats(batches=4)) == 4

    def test_it_is_a_count_of_this_reply_only(self) -> None:
        assert _BY_KEY["requests"].state_class is SensorStateClass.MEASUREMENT


class TestTheSpokenText:
    """The reply rides on the text-length sensor as an attribute: a state is
    capped at 255 characters and a reply is often longer."""

    def test_the_reply_sensor_shows_the_opening_and_carries_the_whole(self) -> None:
        description = _BY_KEY["text"]
        assert description.attributes_fn is not None
        stats = _stats(characters=600, text="好的。" * 200)
        assert description.value_fn(stats) == ("好的。" * 200)[:255]
        assert description.attributes_fn(stats) == {"text": "好的。" * 200}

    def test_an_empty_reply_leaves_it_unknown(self) -> None:
        assert _BY_KEY["text"].value_fn(_stats(text="")) is None

    def test_first_audio_explains_itself(self) -> None:
        description = _BY_KEY["first_audio_ms"]
        assert description.attributes_fn is not None
        stats = _stats(
            first_audio_ms=11457.6, load_ms=8100.2, writer_ms=50.4, inference_ms=3253.6
        )
        assert description.attributes_fn(stats) == {
            "load_ms": 8100,
            "writer_ms": 50,
            "render_ms": 3254,
        }


class TestWhatSitsOnTheMainCard:
    """What was said and how it went are for the person; how the model
    performed is diagnostic."""

    def test_the_outcome_sensors_are_not_diagnostic(self) -> None:
        for key in ("text", "mode", "first_audio_ms", "margin_seconds"):
            assert _BY_KEY[key].entity_category is None, key

    def test_the_measurements_are_diagnostic(self) -> None:
        for key in ("inference_ms", "rtf", "requests", "audio_seconds", "characters"):
            assert _BY_KEY[key].entity_category is not None, key
