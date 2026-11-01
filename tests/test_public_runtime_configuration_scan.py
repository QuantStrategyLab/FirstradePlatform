"""Execute the CI scan steps with controlled tools and synthetic workflow files.

The workflow shells under test are extracted from .github/workflows/ci.yml and
run verbatim; no scan logic is reimplemented here. Negative cases use synthetic
scanner stubs and fixtures so no repository content is modified.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALL_STEP = "Install public configuration scan tools"
CONTRACT_STEP = "Verify public configuration scanner contract offline"
SCAN_STEP = "Reject public runtime configuration bindings"
ASSERT_STEP = "Assert public configuration scan completed"


def step_script(name: str) -> str:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    anchor = f"      - name: {name}\n"
    if workflow.count(anchor) != 1:
        raise AssertionError(f"Expected exactly one workflow step named {name!r}")
    lines = workflow.split(anchor, 1)[1].splitlines(keepends=True)
    body = []
    in_script = False
    for line in lines:
        if line.startswith("      - "):
            break
        if not in_script:
            if line.rstrip() == "        run: |":
                in_script = True
        elif not line.strip():
            body.append(line)
        elif line.startswith("          "):
            body.append(line[10:])
        else:
            break
    if not in_script or not body:
        raise AssertionError(f"Expected a shell script in workflow step {name!r}")
    return "".join(body)


class PublicRuntimeConfigurationScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        cls.ripgrep = shutil.which("rg")
        if not cls.bash or not cls.ripgrep:
            raise RuntimeError("The scan regression tests require bash and ripgrep")
        cls.scan_script = step_script(SCAN_STEP)
        cls.install_script = step_script(INSTALL_STEP)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.workflows = self.root / ".github/workflows"
        self.workflows.mkdir(parents=True)
        self.output = self.root / "step-output.txt"

    def run_script(self, script: str, *, environment=None) -> subprocess.CompletedProcess:
        environment = dict(environment or {})
        environment.setdefault("PATH", str(self.tools))
        environment.setdefault("LC_ALL", "C")
        return subprocess.run(
            [self.bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def run_scan(self, *, with_output=True):
        self.output.unlink(missing_ok=True)
        environment = {}
        if with_output:
            environment["GITHUB_OUTPUT"] = str(self.output)
        return self.run_script(self.scan_script, environment=environment)

    def completion_output(self):
        return self.output.read_text(encoding="utf-8") if self.output.exists() else ""

    def run_completion_check(self, *, outcome="", completed=""):
        return self.run_script(
            step_script(ASSERT_STEP),
            environment={
                "PUBLIC_CONFIG_SCAN_OUTCOME": outcome,
                "PUBLIC_CONFIG_SCAN_COMPLETED": completed,
            },
        )

    def fake_scanner(self, exit_code, output=""):
        executable = self.tools / "rg"
        executable.write_text(
            f"#!{self.bash}\nprintf '%s\\n' '{output}' >&2\nexit {exit_code}\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

    def real_scanner(self):
        os.symlink(self.ripgrep, self.tools / "rg")

    def workflow(self, content):
        (self.workflows / "synthetic.yml").write_text(content, encoding="utf-8")

    def test_contract_step_precedes_scan_step_and_assertion_follows(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        for name in (CONTRACT_STEP, SCAN_STEP, ASSERT_STEP):
            self.assertEqual(workflow.count(f"- name: {name}\n"), 1, name)
        contract = workflow.index(f"- name: {CONTRACT_STEP}")
        scan = workflow.index(f"- name: {SCAN_STEP}")
        assertion = workflow.index(f"- name: {ASSERT_STEP}")
        self.assertLess(contract, scan)
        self.assertLess(scan, assertion)

    def test_missing_executable_fails(self):
        result = self.run_scan()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.completion_output(), "")
        print(f"Expected scanner failure: missing rg; exit={result.returncode}.", flush=True)

    def test_non_executable_scanner_fails(self):
        self.fake_scanner(1)
        (self.tools / "rg").chmod(0o644)
        result = self.run_scan()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.completion_output(), "")

    def test_scanner_errors_fail(self):
        for status in (2, 7, 126, 127, 130):
            with self.subTest(status=status):
                self.fake_scanner(status)
                result = self.run_scan()
                self.assertEqual(result.returncode, status)
                self.assertEqual(self.completion_output(), "")

    def test_error_with_output_fails(self):
        self.fake_scanner(2, "synthetic scanner read error")
        result = self.run_scan()
        self.assertEqual(result.returncode, 2)
        self.assertIn("synthetic scanner read error", result.stderr)
        self.assertEqual(self.completion_output(), "")

    def test_clean_no_match_succeeds(self):
        self.fake_scanner(1)
        result = self.run_scan()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.completion_output(), "scan_completed=true\n")

    def test_match_fails_policy_check(self):
        self.fake_scanner(0)
        result = self.run_scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("must use GitHub Secrets", result.stderr)
        self.assertEqual(self.completion_output(), "")

    def test_real_clean_workflow_succeeds(self):
        self.real_scanner()
        self.workflow("name: Synthetic workflow\n")
        result = self.run_scan()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.completion_output(), "scan_completed=true\n")
        print(
            f"Completed real scanner run: clean workflow; exit={result.returncode}; "
            "completion output verified.",
            flush=True,
        )

    def test_real_repository_workflow_scope_succeeds(self):
        self.real_scanner()
        environment = {
            "PATH": f"{self.tools}:{os.environ.get('PATH', '')}",
            "LC_ALL": "C",
            "GITHUB_OUTPUT": str(self.output),
        }
        result = subprocess.run(
            [self.bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", self.scan_script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.completion_output(), "scan_completed=true\n")
        print(
            "Completed real repository scan: actual workflow scope accepted; "
            "completion output verified.",
            flush=True,
        )

    def test_real_sensitive_variable_bindings_fail(self):
        self.real_scanner()
        names = (
            "GLOBAL_TELEGRAM_CHAT_ID",
            "CLOUD_RUN_SERVICES",
            "CLOUD_RUN_SERVICE",
            "RUNTIME_HEARTBEAT_REQUIRED_SERVICES",
            "RUNTIME_GUARD_SCHEDULER_JOB_PATTERN",
        )
        suffixes = (" }}", "}}", "|| 'synthetic' }}")
        for name in names:
            for suffix in suffixes:
                with self.subTest(name=name, suffix=suffix):
                    self.workflow("value: " + "${{ vars." + name + suffix + "\n")
                    self.assertEqual(self.run_scan().returncode, 1)

    def test_real_secret_and_unrelated_variable_bindings_succeed(self):
        self.real_scanner()
        self.workflow(
            "secret: ${{ secrets.CLOUD_RUN_SERVICE }}\n"
            "unrelated: ${{ vars.CLOUD_RUN_SERVICE_SUFFIX }}\n"
            "generic: ${{ vars.SYNTHETIC_NON_SENSITIVE_SETTING }}\n"
        )
        self.assertEqual(self.run_scan().returncode, 0)

    def test_real_missing_scan_directory_fails(self):
        self.real_scanner()
        self.workflows.rmdir()
        result = self.run_scan()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.completion_output(), "")

    def test_scan_remains_limited_to_workflows(self):
        self.real_scanner()
        self.workflow("name: Synthetic workflow\n")
        (self.root / "out-of-scope.txt").write_text(
            "${{ vars.CLOUD_RUN_SERVICE }}\n", encoding="utf-8"
        )
        self.assertEqual(self.run_scan().returncode, 0)

    def test_completion_assertion_accepts_completed_clean_scan(self):
        self.real_scanner()
        self.workflow("name: Synthetic workflow\n")
        self.assertEqual(self.run_scan().returncode, 0)
        self.assertEqual(self.completion_output(), "scan_completed=true\n")
        self.assertEqual(
            self.run_completion_check(outcome="success", completed="true").returncode, 0
        )

    def test_completion_assertion_rejects_failed_skipped_or_unverified_scan(self):
        for outcome in ("success", "failure", "skipped", "cancelled", ""):
            for completed in ("true", "false", "TRUE", ""):
                if (outcome, completed) == ("success", "true"):
                    continue
                with self.subTest(outcome=outcome, completed=completed):
                    result = self.run_completion_check(
                        outcome=outcome, completed=completed
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("completion was not verified", result.stderr)

    def test_missing_output_target_fails_after_clean_scan(self):
        self.real_scanner()
        self.workflow("name: Synthetic workflow\n")
        result = self.run_scan(with_output=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.completion_output(), "")

    def test_unwritable_output_target_fails(self):
        if os.geteuid() == 0:
            self.skipTest("directory permissions do not constrain root")
        self.real_scanner()
        self.workflow("name: Synthetic workflow\n")
        read_only = self.root / "read-only"
        read_only.mkdir()
        read_only.chmod(0o500)
        result = self.run_script(
            self.scan_script,
            environment={"GITHUB_OUTPUT": str(read_only / "step-output.txt")},
        )
        self.assertNotEqual(result.returncode, 0)

    def test_tool_install_failure_fails_without_certification(self):
        sudo = self.tools / "sudo"
        sudo.write_text(f"#!{self.bash}\nexec \"$@\"\n", encoding="utf-8")
        sudo.chmod(0o755)
        apt_get = self.tools / "apt-get"
        apt_get.write_text(f"#!{self.bash}\nexit 1\n", encoding="utf-8")
        apt_get.chmod(0o755)
        self.output.unlink(missing_ok=True)
        result = self.run_script(
            self.install_script, environment={"GITHUB_OUTPUT": str(self.output)}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.completion_output(), "")
        print(
            f"Expected tool-install failure: synthetic apt-get; exit={result.returncode}; "
            "no completion certification emitted.",
            flush=True,
        )


if __name__ == "__main__":
    unittest.main()
