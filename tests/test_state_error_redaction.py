"""Private legacy state locators never escape through diagnostic exceptions."""
import traceback
from types import SimpleNamespace

import pytest

from application import state_persistence as state


@pytest.mark.parametrize("operation", ["read_json", "write_json", "create_json"])
def test_state_errors_omit_private_locator_and_provider_details(monkeypatch, operation):
    key = "strategy-runs/____4321/synthetic/period/latest.json"

    def fail(*args, **kwargs):
        raise RuntimeError(f"synthetic provider failure: {key} account=87654321 token=synthetic-secret")

    monkeypatch.setattr(state, "_object_store", lambda: SimpleNamespace(read_text=fail, write_text=fail))
    store = state.GcsStateStore(bucket="synthetic", client_factory=fail)
    with pytest.raises(state.StatePersistenceError) as caught:
        method = getattr(store, operation)
        method(key) if operation == "read_json" else method(key, {})
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    for private in (key, "4321", "87654321", "synthetic-secret"):
        assert private not in str(caught.value)
        assert private not in rendered


@pytest.mark.parametrize("payload", ["invalid-json", "[]"])
def test_invalid_state_payload_error_omits_private_locator(monkeypatch, payload):
    monkeypatch.setattr(state, "_object_store", lambda: SimpleNamespace(read_text=lambda _: payload))
    with pytest.raises(state.StatePersistenceError) as caught:
        state.GcsStateStore(bucket="synthetic").read_json("strategy-runs/____4321/latest.json")
    assert "4321" not in str(caught.value)
