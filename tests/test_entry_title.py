"""What an entry is called, once there is more than one server.

The title used to carry the app's version, read from `/health` when the entry
was created. It named what happened to be running that day and never moved
again — a server on 0.2.2 still sat in the list as "Cortex TTS (0.1.0)" — and
it answered the wrong question anyway: with two servers configured, the thing
a title has to say is *which machine*.
"""

from __future__ import annotations

import pytest

from custom_components.cortex_tts.config_flow import DEFAULT_PORT, server_label


class TestWhatNamesAServer:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("http://local-cortex-tts:8771", "local-cortex-tts:8771"),
            ("http://192.168.10.36:8771", "192.168.10.36:8771"),
            ("http://homeassistant.local:8771/", "homeassistant.local:8771"),
            ("http://box:9000", "box:9000"),
        ],
    )
    def test_the_address_is_the_label(self, host: str, expected: str) -> None:
        assert server_label(host) == expected

    def test_an_address_typed_without_a_port_gets_the_default(self) -> None:
        """The title stays something a reader can paste back."""
        assert server_label("192.168.10.36") == f"192.168.10.36:{DEFAULT_PORT}"

    def test_two_servers_get_two_names(self) -> None:
        """The property that matters: the label separates them, and neither
        depends on a version that will move under it."""
        assert server_label("http://local-cortex-tts:8771") != server_label(
            "http://192.168.10.36:8771"
        )
        assert server_label("http://box:8771") != server_label("http://box:9000")
