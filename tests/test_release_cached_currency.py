from pathlib import Path

import pytest

from scripts.release_cached_currency import MODULE, patch_archive


def test_archive_patch_preserves_every_other_byte():
    reviewed = Path(MODULE).read_text()
    original = reviewed.replace('            "currency": balances.get("currency"),\n', "", 1)
    assert original != reviewed
    assert patch_archive(original, reviewed) == reviewed


def test_archive_patch_rejects_already_fixed_and_unknown_code():
    reviewed = Path(MODULE).read_text()
    with pytest.raises(ValueError, match="patch_not_applicable"):
        patch_archive(reviewed, reviewed)
    with pytest.raises(ValueError, match="cached_archive_function_missing"):
        patch_archive("pass\n", reviewed)
