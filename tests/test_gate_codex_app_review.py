from __future__ import annotations

from scripts.gate_codex_app_review import check_metadata, scan_diff


def test_scan_diff_redacts_hardcoded_secret_values() -> None:
    secret_field = "API" + "_KEY"
    secret_value = "super" + "secretvalue123456"
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        f'+{secret_field} = "{secret_value}"\n'
    )

    violations = scan_diff(diff, [])

    assert len(violations) == 1
    assert "<redacted>" in violations[0]
    assert "api_key" in violations[0]
    assert secret_value not in violations[0]


def test_metadata_allows_removing_legacy_dependency_manifests() -> None:
    files = [
        {"filename": "requirements.txt", "status": "removed"},
        {"filename": "constraints.txt", "status": "removed"},
    ]

    assert check_metadata(files, {}) == []


def test_metadata_still_blocks_other_deleted_files() -> None:
    files = [{"filename": "main.py", "status": "removed"}]

    assert check_metadata(files, {}) == ["**File deleted**: `main.py` — verify intentional"]
