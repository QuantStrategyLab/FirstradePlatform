# Cloud Run tag policy excerpt

This directory vendors only `CONTRACT_VERSION`, `TAGGED_URL_BUDGET` and `headroom`
from QuantPlatformKit `src/quant_platform_kit/operators/cloud_run_tags.py`, contract
`cloud-run-tags/v1`, under its MIT license. These definitions are copied verbatim.
The preflight excerpt has no dependencies, allocation journal or traffic API, so
both the workflow's Python runner and the legacy Cloud Build path can use it
without installing or advancing the runtime's pinned shared packages.

The caller validates Cloud Run service syntax and retains the fixed `cbd` contract.
It checks the original value before display masking, never renames or shortens a
service, and refuses diagnostics when the three-character tag does not fit. The
image-only Cloud Build entry point validates service syntax without requiring a
tag. Passing preflight grants no deployment or traffic permission.

When updating, copy the three definitions together from one exact reviewed QPK
revision, update the source pin below, and run the caller boundary tests. Do not
add allocation, ownership journals or micro-split fallback to these diagnostic or
image-only callers. Existing staging baseline, identity, no-traffic, tag ownership
and post-mutation readback checks remain required.

Source pin: local contract snapshot from `qpk-cr-tag-helper-r1`, version
`cloud-run-tags/v1`. The upstream Draft was unavailable at adoption, so this is the
bounded local-copy fallback. Full source snapshot SHA-256:
`ecb8762ddd8e76c1fb6bf4de2981347951ba9640442632b1e3aaa459d5fc8c05`.
Only the three definitions listed above are vendored; no runtime dependency pin
changes are needed. The delivery report retains the upstream availability readback.
