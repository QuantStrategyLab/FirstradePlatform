"""Verify the coverage source layout measures shipped production code.

The denominator is the ``src/firstrade_platform`` package tree minus explicitly
excluded categories. A hermetic synthetic tree proves that unexecuted in-scope
lines and files remain in the denominator while excluded test/external/build
code cannot inflate the measurement.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]

SYNTHETIC_MODULE = (
    "def executed():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def never_executed():\n"
    "    value = 1\n"
    "    return value\n"
)

CANONICAL_OMIT_CATEGORIES = ("tests/", "external/", ".venv/", "build/", "dist/")
LEGACY_ROOT_PATHS = (
    "application",
    "entrypoints",
    "notifications",
    "decision_mapper.py",
    "main.py",
    "runtime_config_support.py",
    "runtime_execution_policy.py",
    "strategy_loader.py",
    "strategy_registry.py",
    "strategy_runtime.py",
)


def pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def coverage_run_config():
    return pyproject()["tool"]["coverage"]["run"]


def coverage_report_config():
    return pyproject()["tool"]["coverage"]["report"]


def declared_production_sources():
    data = pyproject()
    setuptools_cfg = data["tool"]["setuptools"]
    package_dir = setuptools_cfg["package-dir"]
    where = setuptools_cfg["packages"]["find"]["where"]
    if package_dir != {"": "src"} or where != ["src"]:
        raise AssertionError(f"Unexpected setuptools src layout: {package_dir!r}, {where!r}")
    src_root = ROOT / "src"
    packages = sorted(
        p.name for p in src_root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
    )
    return packages


def matches_any_omit(path, omit_patterns):
    for pattern in omit_patterns:
        regex = "".join(".*" if char == "*" else re.escape(char) for char in pattern)
        if re.fullmatch(regex, path):
            return True
    return False


class CoverageSourceLayoutTests(unittest.TestCase):
    def test_source_is_src_package_with_explicit_category_exclusions(self):
        run = coverage_run_config()
        report = coverage_report_config()
        self.assertEqual(run["source"], ["src/firstrade_platform"])
        self.assertTrue(report.get("include_namespace_packages"))
        self.assertTrue((ROOT / "src" / "firstrade_platform" / "__init__.py").is_file())
        for legacy_path in LEGACY_ROOT_PATHS:
            self.assertFalse((ROOT / legacy_path).exists(), legacy_path)
        self.assertNotIn("fail_under", str(pyproject()["tool"]["coverage"]))
        omit = " ".join(run["omit"])
        for category in CANONICAL_OMIT_CATEGORIES:
            self.assertIn(category, omit)

    def test_declared_production_paths_are_measured(self):
        packages = declared_production_sources()
        self.assertEqual(packages, ["firstrade_platform"])
        omit = coverage_run_config()["omit"]
        for package in packages:
            pkg_root = ROOT / "src" / package
            self.assertTrue(pkg_root.is_dir(), package)
            for py_file in sorted(pkg_root.rglob("*.py")):
                relative = py_file.relative_to(ROOT).as_posix()
                self.assertFalse(matches_any_omit(relative, omit), relative)

    def test_unexecuted_in_scope_files_remain_in_denominator(self):
        packages = declared_production_sources()
        package = packages[0]
        run = coverage_run_config()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg_dir = root / "src" / package
            (pkg_dir / "application").mkdir(parents=True)
            (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
            (pkg_dir / "executed_mod.py").write_text(SYNTHETIC_MODULE, encoding="utf-8")
            (pkg_dir / "never_imported.py").write_text(SYNTHETIC_MODULE, encoding="utf-8")
            (pkg_dir / "application" / "__init__.py").write_text("", encoding="utf-8")
            (pkg_dir / "application" / "synthetic_service.py").write_text(
                SYNTHETIC_MODULE, encoding="utf-8"
            )
            excluded = "def never_executed():\n    value = 1\n    return value\n"
            for directory in ("tests", "external", ".venv", "build", "dist"):
                (root / directory).mkdir(parents=True)
                (root / directory / "excluded_module.py").write_text(excluded, encoding="utf-8")
            (root / "runner.py").write_text(
                "import importlib, sys\n"
                "sys.path.insert(0, 'src')\n"
                f"importlib.import_module('{package}.executed_mod').executed()\n"
                "print('done')\n",
                encoding="utf-8",
            )
            (root / ".coveragerc").write_text(
                "[run]\n"
                "branch = true\n"
                "source =\n"
                + "".join(f"    {entry}\n" for entry in run["source"])
                + "omit =\n"
                + "".join(f"    {pattern}\n" for pattern in run["omit"])
                + "[report]\n"
                f"include_namespace_packages = {coverage_report_config().get('include_namespace_packages', False)}\n",
                encoding="utf-8",
            )
            collected = subprocess.run(
                [sys.executable, "-m", "coverage", "run", "runner.py"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(collected.returncode, 0, collected.stderr)
            report = subprocess.run(
                [sys.executable, "-m", "coverage", "report", "--show-missing"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(report.returncode, 0, report.stderr)
            output = report.stdout
            self.assertNotIn("No data to report", output)
            lines = {
                candidate.split()[0]: candidate
                for candidate in output.splitlines()
                if candidate and not candidate.startswith(("Name", "-", "TOTAL"))
            }
            executed_path = f"src/{package}/executed_mod.py"
            self.assertIn(executed_path, lines)
            self.assertIn("6-7", lines[executed_path])
            never_imported_path = f"src/{package}/never_imported.py"
            self.assertIn(never_imported_path, lines)
            self.assertIn("0%", lines[never_imported_path])
            service_path = f"src/{package}/application/synthetic_service.py"
            self.assertIn(service_path, lines)
            self.assertIn("0%", lines[service_path])
            for directory in ("tests", "external", ".venv", "build", "dist"):
                self.assertNotIn(f"{directory}/excluded_module.py", output, directory)

    def test_ci_measurement_step_runs_the_suite_instrumented(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("coverage run -m pytest", workflow)
        self.assertIn("coverage report --show-missing", workflow)


if __name__ == "__main__":
    unittest.main()
