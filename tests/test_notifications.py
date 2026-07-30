import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PLATFORM_KIT_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(PLATFORM_KIT_SRC) not in sys.path:
    sys.path.insert(0, str(PLATFORM_KIT_SRC))

from notifications.telegram import build_sender


class FakeRequests:
    def __init__(self, *, status_code=200, payload=None):
        self.calls = []
        self.status_code = status_code
        self.payload = {"ok": True} if payload is None else payload

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return FakeResponse(self.status_code, self.payload)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_build_sender_breaks_market_symbol_auto_links():
    fake_requests = FakeRequests()
    sender = build_sender("token-1", "chat-1", requests_module=fake_requests)
    assert sender("SOXL.US and 00700.HK") is True

    assert len(fake_requests.calls) == 1
    url, payload, timeout = fake_requests.calls[0]
    assert "token-1" in url
    assert payload["chat_id"] == "chat-1"
    assert payload["text"] == "SOXL.\u2060US and 00700.\u2060HK"
    assert timeout == 15


def test_build_sender_returns_false_when_telegram_rejects_message():
    fake_requests = FakeRequests(payload={"ok": False, "description": "chat not found"})
    sender = build_sender("token-1", "chat-1", requests_module=fake_requests)

    assert sender("rebalance") is False
