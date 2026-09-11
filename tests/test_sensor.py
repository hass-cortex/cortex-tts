"""The diagnostic sensors: what each reads, and when it is cleared."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.hojo_tts.const import FIRST_AUDIO_FIELDS
from custom_components.hojo_tts.models import SpeechStats
from custom_components.hojo_tts.sensor import DESCRIPTIONS, HojoTTSSensor

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
        "streamed": True,
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

    def test_the_mode_reads_as_a_word(self) -> None:
        assert _BY_KEY["streamed"].value_fn(_stats(streamed=True)) == "streamed"
        assert _BY_KEY["streamed"].value_fn(_stats(streamed=False)) == "buffered"
        assert _BY_KEY["streamed"].options is not None


class TestClearing:
    @staticmethod
    def _sensor(key: str) -> HojoTTSSensor:
        sensor = object.__new__(HojoTTSSensor)
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
