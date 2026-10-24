import json

import pytest

from scripts.inspect_funds_snapshot_schema import main, project_schema


def test_schema_never_exports_financial_values_or_identity():
    result = project_schema({
        "as_of": "2026-01-02T03:04:05Z", "account": "PRIVATE_ACCOUNT",
        "balance_metrics": {"cash.available": 9876.543, "buying_power": 5432.10,
                            "account.total_value": 1234.56, "cash.12345678": 42,
                            "secret_cash": "PRIVATE_SECRET", "cash.<script>": 100},
        "positions": [{"symbol": "PRIVATE_HOLDING"}],
    })
    assert result["balance_field_names"] == ["buying_power", "cash.available"]
    assert result["redacted_field_count"] == 4
    assert result["positions_present"] is True
    rendered = json.dumps(result)
    assert all(s not in rendered for s in ["9876", "5432", "PRIVATE", "12345678"])


@pytest.mark.parametrize("snapshot", [None, [], {}, {"balance_metrics": []}])
def test_missing_schema_is_not_reported_as_success(snapshot):
    with pytest.raises(ValueError):
        project_schema(snapshot)


def test_invalid_observation_text_is_not_echoed():
    result = project_schema({"as_of": "PRIVATE_TOKEN", "balance_metrics": {}})
    assert result["observed_at"] is None
    assert result["positions_present"] is False


def test_unexpected_exception_is_not_printed(monkeypatch, capsys):
    def fail():
        raise RuntimeError("PRIVATE_TOKEN")
    monkeypatch.setattr("scripts.inspect_funds_snapshot_schema.inspect_existing_snapshots", fail)
    assert main() == 1
    assert json.loads(capsys.readouterr().out) == {"status": "inspection_unavailable"}
