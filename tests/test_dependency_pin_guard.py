from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from types import ModuleType


SCRIPT = Path("scripts/check_qpk_pin_consistency.py")
CI_WORKFLOW = Path(".github/workflows/ci.yml")


def _load_guard_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_qpk_pin_consistency_for_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_pin_guard_checks_constraints_and_all_qsl_git_refs() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert '"**/constraints*.txt"' in script
    assert "QSL_REF_RE" in script
    assert "inconsistent QuantStrategyLab dependency pin" in script


def test_dependency_pin_guard_rejects_internal_qsl_git_ref_drift(tmp_path, monkeypatch, capsys) -> None:
    module = _load_guard_module()
    qpk_ref = "0" * 40
    old_strategy_ref = "1" * 40
    new_strategy_ref = "2" * 40
    (tmp_path / "requirements.txt").write_text(
        "quant-platform-kit @ git+https://github.com/QuantStrategyLab/QuantPlatformKit.git@" + qpk_ref + "\n"
        "us-equity-strategies @ git+https://github.com/QuantStrategyLab/UsEquityStrategies.git@"
        + old_strategy_ref
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "constraints.txt").write_text(
        "us-equity-strategies @ git+https://github.com/QuantStrategyLab/UsEquityStrategies.git@"
        + new_strategy_ref
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, "fetch_pin", lambda: qpk_ref)
    monkeypatch.setattr(sys, "argv", ["check_qpk_pin_consistency.py"])

    assert module.main() == 1
    output = capsys.readouterr().out
    assert "inconsistent QuantStrategyLab dependency pin for UsEquityStrategies" in output


def test_dependency_pin_guard_is_blocking_in_ci() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    step_start = workflow.index("name: Check QPK pin consistency")
    next_step = workflow.find("\n      - name:", step_start + 1)
    step = workflow[step_start : next_step if next_step != -1 else len(workflow)]

    assert "external/QuantPlatformKit/scripts/check_qpk_pin_consistency.py" in step
    assert 'steps.locked-shared-refs.outputs.quant_platform_kit' in step
    assert '--pin-file "$RUNNER_TEMP/firstrade-qpk-pin"' in step
    assert "continue-on-error" not in step


def test_ci_shared_checkouts_use_locked_refs() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "name: Read locked shared dependency refs" in workflow
    for name, directory in (
        ("quant_platform_kit", "QuantPlatformKit"),
        ("us_equity_strategies", "UsEquityStrategies"),
    ):
        assert f"ref: ${{{{ steps.locked-shared-refs.outputs.{name} }}}}" in workflow
        assert f'git -C external/{directory} rev-parse HEAD' in workflow
    assert "git ls-remote" not in workflow
    assert "uv pip install --no-deps -e" not in workflow
    assert "uv sync --frozen --extra test" in workflow
    assert "uv pip check" in workflow
    assert "uv lock --check" in workflow


def test_ci_installed_shared_identity_matches_lock(monkeypatch) -> None:
    import importlib.metadata
    import json
    import tomllib

    import pytest

    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    smoke = workflow.split("name: Smoke import pinned shared packages", 1)[1]
    code = textwrap.dedent(smoke.split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0])
    refs = {
        package["name"]: package["source"]["git"].rsplit("#", 1)[1]
        for package in tomllib.loads(Path("uv.lock").read_text())["package"]
        if package["name"] in {"quant-platform-kit", "us-equity-strategies"}
    }
    identities = {name: {"vcs_info": {"commit_id": ref}} for name, ref in refs.items()}

    class Distribution:
        def __init__(self, name):
            self.name = name

        def read_text(self, filename):
            assert filename == "direct_url.json"
            return json.dumps(identities[self.name])

    monkeypatch.setattr(importlib.metadata, "distribution", Distribution)
    exec(code, {})
    for name in refs:
        for invalid in ({"vcs_info": {"commit_id": "0" * 40}}, {"dir_info": {"editable": True}}):
            with monkeypatch.context() as context:
                context.setitem(identities, name, invalid)
                with pytest.raises(AssertionError):
                    exec(code, {})
