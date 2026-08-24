from application.strategy_run_persistence import (
    claim_live_strategy_run,
    strategy_run_claim_key,
)


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
