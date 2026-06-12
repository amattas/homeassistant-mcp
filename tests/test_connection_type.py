"""Tests for HomeAssistantService._detect_connection_type host matching"""

import pytest

# The service module requires project dependencies; skip cleanly when absent
pytest.importorskip("requests")
pytest.importorskip("redis")

from src.services.homeassistant import (
    ConnectionType,
    HomeAssistantService,
)  # noqa: E402


def _service(url: str) -> HomeAssistantService:
    return HomeAssistantService(url=url, access_token="test-token")


class TestDetectConnectionType:
    @pytest.mark.parametrize(
        "url",
        [
            "https://ui.nabu.casa",
            "https://example.ui.nabu.casa",
            "https://UI.NABU.CASA",
            "https://remote.nabucasa.com",
            "https://abc123.remote.nabucasa.com:443",
            "https://user:pass@example.ui.nabu.casa/api",
        ],
    )
    def test_nabu_casa_hosts(self, url):
        assert _service(url).connection_type is ConnectionType.NABU_CASA

    @pytest.mark.parametrize(
        "url",
        [
            "https://homeassistant.local:8123",
            "http://192.168.1.10:8123",
            # CodeQL py/incomplete-url-substring-sanitization bypasses:
            "https://ui.nabu.casa.evil.com",
            "https://remote.nabucasa.com.attacker.net",
            "https://evil.com/ui.nabu.casa",
            "https://notui.nabu.casa",
        ],
    )
    def test_local_hosts(self, url):
        assert _service(url).connection_type is ConnectionType.LOCAL
