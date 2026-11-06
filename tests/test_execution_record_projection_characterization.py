"""ARCH-04A characterization of the public cycle, isolated from suite doubles.

The oracle was frozen against base 79b0237d1a2aac2c602e86751e3064fe29b3ed97,
source blob cf1bfed08e56dbfecdaa1b39530e95bf764136fa, before extraction.
The module AST digest binds the inlined candidate to that actual base. Expected
values/traces below were executed against the unchanged public cycle first.

Only synthetic ports are exercised. Each group runs in a fresh interpreter;
the repeat group also runs adjacent tests in that same interpreter. Boundary
tracing substitutes adversarial recorder inputs that the cycle's normal dict
construction cannot produce. It does not change the function's code or skip
its preceding operations. Traceback frame equivalence is deliberately excluded.
"""
from __future__ import annotations

import ast
from contextlib import ExitStack
import copy
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest


BASE_AST_SHA256 = "a0343fa3854e979b7bd1201be1e2be33690c9532102b64081d77c4e1e06b1c04"
NORMAL_RETURN_SHA256 = (
    '2aa0470430c361939caab46622fae26d575be74dfacb2f1a56c3257fc1578763',
    '50815354287ed7480ba89f29b8aa35a3a59557c6de1723ab47553f06fd170949',
    'bf08a367a10fe09b4fb3fe4033078ad4256385143f3ecf52de77b932b4e71f32',
    'a183132286d2ac79ca624904b777f0993a3a22294df703b303635858cde79d5f',
    '14ccfb506a17284e2ec63a6839d5e60e3cd5bcc12eab35bae30699730a50ebc0',
)
KEYS = (
    "platform", "action_done", "broker_submission_done", "execution_status",
    "orders_pending_count", "strategy_run_stage", "dry_run_only",
    "submitted_orders", "skipped_orders", "error", "total_equity",
)
GROUPS = ("normal", "early", "lists", "fields", "equity", "trace",
          "failures", "ordering", "repeat")
SOURCE = Path(__file__).resolve().parents[1] / "application/rebalance_service.py"


def test_projection_expands_to_frozen_base_ast():
    """A04-11: normalize the complete module, including the recorder call site."""
    tree = ast.parse(SOURCE.read_text())
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "_build_platform_execution_record"]
    if helpers:
        assert len(helpers) == 1
        helper = helpers[0]
        assert ast.dump(helper.args) == ast.dump(ast.parse(
            "def f(result, *, settings): pass").body[0].args)
        assert len(helper.body) == 1 and isinstance(helper.body[0], ast.Return)
        tree.body.remove(helper)
        cycle = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == "run_strategy_cycle")
        call = cycle.body[-2].value
        assert isinstance(call, ast.Call) and call.func.id == "try_record_platform_execution"
        assert ast.dump(call.args[1]) == ast.dump(ast.parse(
            "_build_platform_execution_record(result, settings=settings)", mode="eval").body)
        call.args[1] = helper.body[0].value
    for node in ast.walk(tree):
        for field, value in list(ast.iter_fields(node)):
            if value == []:
                delattr(node, field)
    normalized = ast.dump(tree, include_attributes=False).encode()
    assert hashlib.sha256(normalized).hexdigest() == BASE_AST_SHA256


@pytest.mark.parametrize("group", GROUPS)
def test_public_cycle_characterization(group):
    # Do not inherit credentials, plugin autoload, or the parent's sys.modules.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env.update(HOME=str(Path(__file__).parent / "nonexistent-home"),
               GOOGLE_APPLICATION_CREDENTIALS="/nonexistent-arch04a-adc.json",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONPATH=str(SOURCE.parents[1]))
    outcome = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--exercise", group],
        cwd=SOURCE.parents[1], env=env, text=True, capture_output=True, timeout=90,
    )
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    receipt = next(line for line in outcome.stdout.splitlines()
                   if line.startswith("ARCH04A_OBSERVATION "))
    print(receipt)


def _deny_external_access():
    """Persistent child-only audit guard, active before production imports."""
    def guard(event, args):
        if event.startswith("socket.") and event not in {"socket.__new__"}:
            raise AssertionError("network access denied in characterization")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.fsdecode(args[0]).replace("\\", "/")
            if any(part in path.split("/") for part in
                   (".config", ".aws", ".azure", ".ssh", ".codex", ".secrets")):
                raise AssertionError("credential-directory access denied")
    sys.addaudithook(guard)
    with pytest.raises(AssertionError, match="network access denied"):
        socket.getaddrinfo("example.invalid", 443)
    with pytest.raises(AssertionError, match="credential-directory access denied"):
        open(Path.home() / ".config/gcloud/application_default_credentials.json")


class EvaluationFailure(RuntimeError):
    pass


class Events:
    def __init__(self, fail_at=None):
        self.trace = []
        self.fail_at = fail_at
        self.failure = EvaluationFailure("synthetic evaluation failure")

    def hit(self, label):
        self.trace.append(label)
        if len(self.trace) == self.fail_at:
            raise self.failure


class TracedPortfolio(dict):
    def __init__(self, values, events):
        super().__init__(values)
        self.events = events

    def __bool__(self):
        self.events.hit("portfolio:bool")
        return bool(len(self))

    def get(self, key, default=None):
        self.events.hit("portfolio:get:" + key)
        return super().get(key, default)


class TracedResult(dict):
    def __init__(self, values, events, portfolios=None):
        super().__init__(values)
        self.events = events
        self.portfolios = iter(portfolios) if portfolios is not None else None

    def get(self, key, default=None):
        self.events.hit("result:get:" + key)
        if key == "portfolio" and self.portfolios is not None:
            return next(self.portfolios)
        return super().get(key, default)


class Cycle:
    """Replace external boundaries, retaining the original public orchestrator."""
    def __init__(self, module, *, dry=True, persist=True, early=None,
                 notification=True, store_failure=False, notify_failure=False,
                 injected=None, events=None, portfolios=None, profile_swap=False):
        self.module = module
        self.callbacks = []
        self.records = []
        self.events = events or Events()
        self.result = None
        self.before_record = None
        self.injected = injected
        self.portfolios = portfolios
        self.boundary = False
        self.store_failure = store_failure
        self.notify_failure = notify_failure
        self.failure = EvaluationFailure("synthetic callback failure")
        self.early = early
        self.notification = notification
        self.profile_swap = profile_swap
        cycle = self

        class Settings(SimpleNamespace):
            @property
            def dry_run_only(self):
                if cycle.boundary:
                    cycle.events.hit("settings:dry_run_only")
                return dry

        class Runtime(SimpleNamespace):
            @property
            def profile(self):
                if cycle.boundary:
                    cycle.events.hit("runtime:profile")
                    if cycle.profile_swap:
                        module.try_record_platform_execution = cycle.forbidden
                return "synthetic_profile"

        self.settings = Settings(
            notify_lang="en", strategy_profile="synthetic_profile",
            strategy_display_name="Synthetic", strategy_plugin_mounts_json=None,
            runtime_target=None, persist_strategy_runs=persist,
            live_trading_enabled=not bool(dry), live_order_ack=False,
            max_order_notional_usd=25, cash_only_execution=True,
            reserved_cash_floor_usd=0, reserved_cash_ratio=0,
            safe_haven_cash_substitute_threshold_usd=0,
        )
        self.runtime = Runtime(
            display_name="Synthetic", managed_symbols=(),
            runtime_adapter=SimpleNamespace(available_inputs=()), merged_runtime_config={},
            evaluate=lambda **_: self.callback("evaluate", SimpleNamespace(
                decision=SimpleNamespace(diagnostics={}), metadata={})),
        )
        self.snapshot = SimpleNamespace(cash_balance=100, metadata={},
                                        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.execution = SimpleNamespace(
            idempotency_blocked=early == "execution_claim", submitted_orders=[],
            skipped_orders=[], execution_notes=[], action_done=False,
            broker_submission_done=False, pending_reconciliation=False,
        )
        self.plan = {"portfolio": {"total_equity": 123, "equity": 456}}
        self.client = SimpleNamespace(
            session_reused=False,
            select_account=lambda _: self.callback("select_account", "synthetic-account"),
        )
        self.adapters = SimpleNamespace(
            build_market_data_port=lambda: object(),
            build_execution_port=lambda: object(),
            build_portfolio_port=lambda: SimpleNamespace(
                get_portfolio_snapshot=lambda: self.callback("snapshot", self.snapshot)),
        )

    def forbidden(self, *args, **kwargs):
        raise AssertionError("unexpected external boundary")

    def callback(self, name, value=None):
        self.callbacks.append(name)
        return value

    def persist(self, **kwargs):
        self.callback("persist:" + kwargs["state"]["stage"])
        if self.store_failure:
            raise self.failure
        return True

    def notify(self, result, **kwargs):
        self.result = result
        self.callback("notify")
        if self.notify_failure:
            raise self.failure
        return True

    def record(self, profile, payload):
        self.events.hit("recorder")
        self.callback("record")
        self.records.append((profile, payload))

    def run(self):
        module = self.module
        cycle_ast = next(n for n in ast.parse(SOURCE.read_text()).body
                         if isinstance(n, ast.FunctionDef) and n.name == "run_strategy_cycle")
        recorder_line = cycle_ast.body[-2].lineno
        original_trace = sys.gettrace()

        def trace(frame, event, arg):
            if (event == "line" and frame.f_code is module.run_strategy_cycle.__code__
                    and frame.f_lineno == recorder_line and not self.boundary):
                original = frame.f_locals["result"]
                if self.injected is not None:
                    original = TracedResult(self.injected, self.events, self.portfolios)
                    frame.f_locals["result"] = original
                self.result = original
                self.before_record = dict(original)
                self.boundary = True
            return trace

        replacements = dict(
            _utcnow=lambda: self.snapshot.as_of,
            load_strategy_plugin_signals=lambda *a, **k: ([], None),
            dca_execution_unsupported_reason=lambda _: "synthetic unsupported"
            if self.early == "unsupported" else None,
            _connect_client=lambda **_: self.callback("connect", self.client),
            build_gcs_state_store_from_env=self.forbidden,
            load_strategy_runtime=lambda *a, **k: self.runtime,
            build_runtime_broker_adapters=lambda **_: self.adapters,
            attach_strategy_plugin_metadata=lambda snapshot, _: snapshot,
            build_market_inputs=lambda **_: {},
            build_strategy_evaluation_inputs=lambda **_: {},
            map_strategy_decision_to_plan=lambda *a, **k: self.callback("plan", self.plan),
            substitute_small_safe_haven_targets_with_cash=lambda plan, **_: plan,
            resolve_strategy_run_period=lambda **_: "synthetic-period",
            mask_account_id=lambda _: "synthetic-masked",
            legacy_account_state_id=lambda _: "synthetic-state",
            read_latest_strategy_run_state=lambda **_: self.callback("read_state", None),
            read_live_strategy_run_claim=lambda **_: self.callback("read_claim", None),
            has_effective_live_submission_claim=lambda _: self.early == "duplicate_claim",
            is_duplicate_live_run=lambda _: self.early == "duplicate_run",
            build_strategy_run_state=lambda **kwargs: self.callback(
                "state:" + kwargs["stage"], kwargs),
            persist_strategy_run_state=self.persist,
            execute_value_target_plan=lambda **_: self.callback("execute", self.execution),
            build_signal_snapshot=lambda **_: {},
            attach_strategy_plugin_result=lambda result, **_: self.callback("attach", result),
            _report_unarchived_direct_coverage=lambda *a, **k: self.callback("coverage", False),
            _should_publish_cycle_notification=lambda _: self.callback("policy", True),
            _publish_cycle_notification=self.notify,
            try_record_platform_execution=self.record,
        )
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(module, **replacements))
            try:
                sys.settrace(trace)
                result = module.run_strategy_cycle(
                    runtime_settings=self.settings, credentials=object(),
                    client_factory=self.forbidden, state_store=object(),
                    notification_sender=self.forbidden,
                    env_reader=lambda name, default=None: default,
                    send_cycle_notification=self.notification, dispatch_plugin_alerts=False,
                )
            finally:
                sys.settrace(original_trace)
        if self.boundary:
            assert result is self.result
            assert dict(result) == self.before_record  # Recorder construction does not mutate input.
        return result


PRECEDING = ["connect", "select_account", "snapshot", "evaluate", "plan",
             "state:ORDERS_PLANNED", "persist:ORDERS_PLANNED", "execute", "attach",
             "state:DRY_RUN_COMPLETED", "persist:DRY_RUN_COMPLETED", "coverage", "policy", "notify"]
READS = ["runtime:profile", "result:get:action_done", "result:get:broker_submission_done",
         "result:get:execution_status", "result:get:orders_pending_count",
         "result:get:strategy_run_stage", "settings:dry_run_only",
         "result:get:submitted_orders", "result:get:skipped_orders", "result:get:error"]


def _payload(cycle, *, equity=123):
    assert len(cycle.records) == 1
    profile, payload = cycle.records[0]
    assert profile == "synthetic_profile"
    assert tuple(payload) == KEYS
    assert payload is not cycle.result
    for key in KEYS[1:6] + KEYS[7:10]:
        assert payload[key] is dict.get(cycle.result, key)
    assert payload["dry_run_only"] is cycle.settings.dry_run_only
    assert payload["total_equity"] == equity
    return payload


def _exercise(group, module):
    if group == "normal":
        for index, (dry, stage) in enumerate(((True, "DRY_RUN_COMPLETED"), (False, "NO_ACTION"),
                           (None, "NO_ACTION"), (0, "NO_ACTION"), ("preview", "DRY_RUN_COMPLETED"))):
            cycle = Cycle(module, dry=dry)
            result = cycle.run()
            assert hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest() == (
                NORMAL_RETURN_SHA256[index])
            payload = _payload(cycle)
            assert list(payload.items()) == list(zip(KEYS, (
                "firstrade", False, False, "", 0, stage, dry, [], [], None, 123)))
            expected = list(PRECEDING)
            if not dry:
                expected[5:5] = ["read_state", "read_claim"]
            expected = [v.replace(":DRY_RUN_COMPLETED", ":" + stage) for v in expected]
            assert cycle.callbacks == expected + ["record"]
            assert result["notification_sent"] is True
            assert result["portfolio"] is cycle.plan["portfolio"]
        # A live-shaped execution result is still produced entirely by synthetic ports.
        cycle = Cycle(module, dry=False)
        order = {"symbol": "SYNTHETIC", "status": "pending"}
        cycle.execution.submitted_orders = [order]
        cycle.execution.action_done = True
        cycle.execution.broker_submission_done = True
        cycle.execution.pending_reconciliation = True
        result = cycle.run()
        payload = _payload(cycle)
        assert payload["submitted_orders"] is result["submitted_orders"]
        assert payload["submitted_orders"][0] is order
        assert payload["dry_run_only"] is False
        assert payload["strategy_run_stage"] == "PENDING_RECONCILIATION"
        assert payload["execution_status"] == "pending_reconciliation"
        assert payload["orders_pending_count"] == 1
        assert payload["action_done"] is payload["broker_submission_done"] is True
    elif group == "early":
        for early in ("unsupported", "duplicate_claim", "duplicate_run", "execution_claim"):
            cycle = Cycle(module, dry=False, early=early)
            result = cycle.run()
            assert cycle.records == [] and cycle.events.trace == []
            assert cycle.callbacks[-1] == "attach"
            if early == "unsupported":
                assert cycle.callbacks == ["attach"]
                assert result["status"] == "skipped"
            else:
                assert result["idempotency_skipped"] is True
                assert "coverage" not in cycle.callbacks and "notify" not in cycle.callbacks
                assert ("execute" in cycle.callbacks) == (early == "execution_claim")
                assert sum(s.startswith("persist:") for s in cycle.callbacks) == (
                    0 if early == "duplicate_claim" else 1)
    elif group == "lists":
        for key in ("submitted_orders", "skipped_orders"):
            for value in ([], [{"symbol": "SYNTHETIC"}], None, "missing"):
                values = {} if value == "missing" else {key: value}
                cycle = Cycle(module, injected=values)
                cycle.run()
                payload = _payload(cycle, equity=None)
                assert payload[key] is (None if value == "missing" else value)
    elif group == "fields":
        for key in ("action_done", "broker_submission_done", "execution_status",
                    "strategy_run_stage", "orders_pending_count", "error"):
            for value in (None, False, 0, "", [], {}, {"malformed": [1]}, "unknown"):
                cycle = Cycle(module, injected={key: value})
                cycle.run()
                assert _payload(cycle, equity=None)[key] is value
    elif group == "equity":
        cases = [({}, None), ({"portfolio": None}, None), ({"portfolio": {}}, None),
                 ({"portfolio": False}, None), ({"portfolio": []}, None)]
        for total in (0, False, None, 7, "unknown", [], {"opaque": 1}):
            cases.append(({"portfolio": {"total_equity": total, "equity": 89}}, total or 89))
        cases += [({"portfolio": {"equity": 0}}, 0),
                  ({"portfolio": {"total_equity": 0}}, None)]
        for values, expected in cases:
            before = copy.deepcopy(values)
            cycle = Cycle(module, injected=values)
            cycle.run()
            _payload(cycle, equity=expected)
            assert values == before
        for bad in (1, "truthy", [1]):
            cycle = Cycle(module, injected={"portfolio": bad})
            with pytest.raises(AttributeError, match="has no attribute 'get'"):
                cycle.run()
            assert cycle.callbacks == PRECEDING and cycle.records == []
    elif group in {"trace", "failures"}:
        for total in (0, 7):
            expected = READS + ["result:get:portfolio", "portfolio:bool",
                                "portfolio:get:total_equity"]
            if not total:
                expected += ["result:get:portfolio", "portfolio:bool", "portfolio:get:equity"]
            expected += ["recorder"]
            failures = [None] if group == "trace" else range(1, len(expected) + 1)
            for fail_at in failures:
                events = Events(fail_at)
                portfolio = TracedPortfolio({"total_equity": total, "equity": 89}, events)
                cycle = Cycle(module, injected={"portfolio": portfolio}, events=events)
                if fail_at is None:
                    cycle.run()
                    assert events.trace == expected
                    _payload(cycle, equity=total or 89)
                    assert cycle.callbacks == PRECEDING + ["record"]
                else:
                    with pytest.raises(EvaluationFailure) as caught:
                        cycle.run()
                    assert caught.value is events.failure
                    assert str(caught.value) == "synthetic evaluation failure"
                    assert events.trace == expected[:fail_at]
                    assert cycle.callbacks == PRECEDING and cycle.records == []
        if group == "trace":
            events = Events()
            first = TracedPortfolio({"total_equity": 0}, events)
            second = TracedPortfolio({"equity": 91}, events)
            cycle = Cycle(module, injected={}, events=events, portfolios=[first, second])
            cycle.run()
            _payload(cycle, equity=91)
            cycle = Cycle(module, profile_swap=True)
            cycle.run()  # The callable was looked up before evaluating the profile getter.
            _payload(cycle)
    elif group == "ordering":
        for persist in (False, True):
            for notify in (False, True):
                for store_failure in (False, True):
                    for notify_failure in (False, True):
                        cycle = Cycle(module, persist=persist, notification=notify,
                                      store_failure=store_failure, notify_failure=notify_failure)
                        result = cycle.run()
                        _payload(cycle)
                        expected = PRECEDING[:]
                        if not persist:
                            expected = [v for v in expected if not v.startswith(("state:", "persist:"))]
                        if not notify:
                            expected = [v for v in expected if v not in ("coverage", "policy", "notify")]
                        assert cycle.callbacks == expected + ["record"]
                        if persist and store_failure:
                            assert result["strategy_run_persisted"] is False
                            assert result["strategy_run_persistence_error"] == (
                                "EvaluationFailure: synthetic callback failure")
                        if notify and notify_failure:
                            assert result["notification_sent"] is False
                            assert result["notification_error"] == "EvaluationFailure: synthetic callback failure"
    elif group == "repeat":
        originals = dict(vars(module))
        first = Cycle(module)
        first.run()
        payload = _payload(first)
        payload["submitted_orders"].append({"synthetic": True})
        second = Cycle(module)
        second.run()
        assert _payload(second)["submitted_orders"] == []
        assert first.result is not second.result
        assert all(getattr(module, name) is value for name, value in originals.items())
        # Adjacent cases import the same real module, with lifecycle I/O still fake.
        with patch.object(module, "try_record_platform_execution", lambda *_: None):
            assert pytest.main(["-q", "tests/test_rebalance_service.py",
                                "tests/test_strategy_run_claim.py"]) == 0
        _exercise("normal", module)


def _inventory():
    modules = []
    for name, module in sorted(sys.modules.items()):
        if name.split(".")[0] not in {"quant_platform_kit", "us_equity_strategies"}:
            continue
        path = getattr(module, "__file__", None)
        assert path is not None, name
        source = Path(path).resolve()
        assert source.is_file(), name
        modules.append(dict(name=name, origin=str(source),
                            sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    distributions = {}
    for name in ("quant-platform-kit", "us-equity-strategies"):
        dist = metadata.distribution(name)
        distributions[name] = dict(version=dist.version,
                                   direct_url=json.loads(dist.read_text("direct_url.json")))
    return dict(modules=modules, distributions=distributions)


if __name__ == "__main__":
    assert len(sys.argv) == 3 and sys.argv[1] == "--exercise"
    _deny_external_access()
    import application.rebalance_service as module
    before = _inventory()
    _exercise(sys.argv[2], module)
    after = _inventory()
    print("ARCH04A_OBSERVATION " + json.dumps(dict(
        group=sys.argv[2], before=before, after=after,
        module_doubles=[], attribute_doubles="Cycle.run replacements on the real module",
        limits="Loaded-module snapshots before/after exercises; no transient-import certification; "
               "not the ARCH-06 strict lane; adjacent tests may install their own scoped doubles",
    ), sort_keys=True))
