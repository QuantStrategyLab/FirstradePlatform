from application.strategy_run_persistence import (
    claim_live_strategy_run,
    strategy_run_claim_key,
)
from application.state_persistence import GcsStateStore


class AtomicFakeStore:
    def __init__(self):
        self.payloads = {}

    def create_json(self, key, payload):
        if key in self.payloads:
            return False
        self.payloads[key] = dict(payload)
        return True


def test_live_claim_is_create_only_and_permanent():
    store = AtomicFakeStore()
    kwargs = {
        "store": store,
        "account": "****1234",
        "strategy_profile": "tqqq_core",
        "run_period": "2026-08",
    }

    assert claim_live_strategy_run(**kwargs) is True
    assert claim_live_strategy_run(**kwargs) is False
    key = strategy_run_claim_key(
        account="****1234", strategy_profile="tqqq_core", run_period="2026-08"
    )
    assert store.payloads[key]["stage"] == "PENDING_SUBMISSION"
    assert store.payloads[key]["no_order_submitted"] is True


def test_gcs_state_store_create_json_uses_generation_zero_precondition():
    class FakeBlob:
        def __init__(self):
            self.payload = None
            self.content_type = None
            self.if_generation_match = None

        def upload_from_string(self, payload, *, content_type, if_generation_match):
            self.payload = payload
            self.content_type = content_type
            self.if_generation_match = if_generation_match

    class FakeBucket:
        def __init__(self, blob):
            self._blob = blob
            self.name = ""
            self.object_name = ""

        def blob(self, object_name):
            self.object_name = object_name
            return self._blob

    class FakeClient:
        def __init__(self, bucket):
            self._bucket = bucket

        def bucket(self, name):
            self._bucket.name = name
            return self._bucket

    blob = FakeBlob()
    bucket = FakeBucket(blob)
    store = GcsStateStore(
        bucket="state-bucket",
        prefix="runtime",
        client_factory=lambda: FakeClient(bucket),
    )

    assert store.create_json("claims/run.json", {"state": "claimed"}) is True
    assert bucket.name == "state-bucket"
    assert bucket.object_name == "runtime/claims/run.json"
    assert blob.content_type == "application/json"
    assert blob.if_generation_match == 0
