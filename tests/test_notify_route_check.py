from scripts import notify_route_check as nrc


def test_binding_reports_secret_name_only():
    spec = {"containers": [{"image": "img@sha256:abc", "env": [
        {"name": "TELEGRAM_TOKEN", "valueFrom": {"secretKeyRef": {"name": "quant-sentinel-telegram-bot-token", "key": "latest"}}},
        {"name": "OTHER", "value": "x"},
    ]}]}
    assert nrc._telegram_binding(spec) == {
        "telegram_token_source": "secret_ref",
        "telegram_token_secret": "quant-sentinel-telegram-bot-token",
        "telegram_token_secret_version": "latest",
    }


def test_plain_env_value_is_not_echoed():
    spec = {"containers": [{"env": [{"name": "TELEGRAM_TOKEN", "value": "123:secret"}]}]}
    out = nrc._telegram_binding(spec)
    assert out["telegram_token_source"] == "plain_env_value"
    assert "123:secret" not in repr(out)


def test_revision_summary_traffic_and_ready():
    rev = {
        "metadata": {"name": "svc-00001-abc", "labels": {"commit-sha": "deadbeef"}},
        "spec": {"serviceAccountName": "rt@p.iam.gserviceaccount.com", "containers": [{"image": "i"}]},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }
    out = nrc._revision_summary(rev, {"svc-00001-abc": 100})
    assert out["ready"] == "True"
    assert out["traffic_percent"] == 100
    assert out["telegram_token_source"] == "missing"
