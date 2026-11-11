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


def test_rollout_preserves_existing_tags_and_runtime_configuration():
    import copy

    from scripts.release_cached_currency import rollout

    path = "projects/example/locations/example/services/example"
    base = "example.invalid/app@sha256:" + "1" * 64
    next_image = "example.invalid/app@sha256:" + "2" * 64
    template = {"labels": {"release": "base"}, "serviceAccount": "example", "containers": [{
        "image": base, "env": [{"name": "FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT", "value": "true"},
                               {"name": "RUNTIME_TARGET_ENABLED", "value": "true"}]}]}
    pin = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": "example-old", "percent": 100}
    tag = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": "example-test", "tag": "diag"}
    service = {"ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY", "traffic": [pin, tag], "etag": "old",
               "trafficStatuses": [{"revision": "example-old", "percent": 100}, {"revision": "example-test", "tag": "diag"}],
               "template": copy.deepcopy(template)}
    writes = []

    def request(ref, method="GET", payload=None):
        if method == "PATCH":
            writes.append((ref, copy.deepcopy(payload)))
            assert payload["etag"] == service["etag"]
            if ref.endswith("updateMask=template"):
                service.update(template=payload["template"], latestCreatedRevision="example-new", etag="staged")
            if ref.endswith("updateMask=traffic"):
                service.update(traffic=payload["traffic"], trafficStatuses=[
                    {"revision": "example-new", "percent": 100}, {"revision": "example-test", "tag": "diag"}])
            return {"done": True}
        if ref == path:
            return copy.deepcopy(service)
        if ref.endswith("/example-old"):
            return copy.deepcopy(template)
        assert ref.endswith("/example-new")
        return {**copy.deepcopy(service["template"]), "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}

    rollout(request, path, lambda image: next_image if image == base else "")
    assert len(writes) == 4
    assert service["traffic"][1] == tag
    assert service["template"]["containers"][0]["env"] == template["containers"][0]["env"]
    assert service["template"]["serviceAccount"] == template["serviceAccount"]
