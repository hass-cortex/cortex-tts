"""The diagnostic sensors: what each reads, and when it is cleared."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.cortex_tts.const import FIRST_AUDIO_FIELDS, STREAM_MODES
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
        "mode": "coalesced",
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

    def test_every_sensor_is_diagnostic_and_translated(self) -> None:
        for description in DESCRIPTIONS:
            assert description.entity_category is not None
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
        for mode in STREAM_MODES:
            assert description.value_fn(_stats(mode=mode)) == mode
            assert mode in description.options

    def test_every_mode_the_integration_can_report_is_offered(self) -> None:
        """The sensor's list and the setting's list are the same three words."""
        assert set(_BY_KEY["mode"].options or ()) == set(STREAM_MODES)


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
        """Unlike characters, zero requests cannot have produced a reply."""
        assert _BY_KEY["requests"].value_fn(SpeechStats(success=True)) is None


class TestRequests:
    """What the mode sensor cannot say: whether the grouping grouped anything.

    Measured over 308 production voice replies, 70% were a single sentence.
    Each of those is one request however the model is set, and the mode alone
    reads as though several went out.
    """

    def test_a_single_piece_reply_says_one(self) -> None:
        stats = _stats(mode="coalesced", requests=1)
        assert _BY_KEY["requests"].value_fn(stats) == 1
        assert _BY_KEY["mode"].value_fn(stats) == "coalesced"

    def test_a_grouped_reply_says_how_many(self) -> None:
        assert _BY_KEY["requests"].value_fn(_stats(requests=4)) == 4

    def test_it_is_a_count_of_this_reply_only(self) -> None:
        assert _BY_KEY["requests"].state_class is SensorStateClass.MEASUREMENT
