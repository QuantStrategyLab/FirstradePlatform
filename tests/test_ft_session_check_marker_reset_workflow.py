from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ft-session-check-marker-reset.yml").read_text(
    encoding="utf-8"
)


def test_marker_path_inputs_are_parameterized() -> None:
    for name in ("strategy_profile:", "year_month:", "backup_suffix:"):
        assert name in WORKFLOW
    assert "session-checks/auto/ibit_smart_dca/2026_10" not in WORKFLOW
    assert '${STRATEGY_PROFILE_SEGMENT}/${YEAR_MONTH}' in WORKFLOW


def test_marker_path_inputs_are_validated_before_use() -> None:
    assert '"${STRATEGY_PROFILE_SEGMENT}" =~ ^[a-z0-9_]+$' in WORKFLOW
    assert '"${YEAR_MONTH}" =~ ^[0-9]{4}_[0-9]{2}$' in WORKFLOW
    assert '"${BACKUP_SUFFIX}" =~ ^[0-9]{8}$' in WORKFLOW
    assert WORKFLOW.index('[[ "${STRATEGY_PROFILE_SEGMENT}"') < WORKFLOW.index('base="gs://')
