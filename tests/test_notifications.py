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
    def __init__(self):
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return object()


def test_build_sender_breaks_market_symbol_auto_links():
    fake_requests = FakeRequests()
    sender = build_sender("token-1", "chat-1", requests_module=fake_requests)
    sender("SOXL.US and 00700.HK")

    assert len(fake_requests.calls) == 1
    url, payload, timeout = fake_requests.calls[0]
    assert "token-1" in url
    assert payload["chat_id"] == "chat-1"
    assert payload["text"] == "SOXL.\u2060US and 00700.\u2060HK"
    assert timeout == 15
