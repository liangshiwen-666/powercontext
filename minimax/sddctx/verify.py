"""Execute the business project's test suites and persist auditable evidence.

The verifier is the component that produces the "code change is effective"
evidence: the *same* test files are executed against the pre-change tree and
the post-change tree, and both transcripts (command, exit code, counts, full
stdout, duration) are written to ``evidence/`` as JSON logs.

JUnit XML is the source of truth for "same tests": :func:`evaluate` compares the
``classname::name`` testcase sets of the before/after runs (not just counts or
command strings), so a renamed/removed/added test flips the check to FAIL.

The before tree is produced with the pure-Python unified-diff engine in
:mod:`sddctx.patch`; no external ``patch``/``git`` binary is required.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .patch import reverse_patch

_PASSED = re.compile(r"(\d+) passed")
_FAILED = re.compile(r"(\d+) failed")
_ERRORS = re.compile(r"(\d+) errors?")
_SKIPPED = re.compile(r"(\d+) skipped")

TARGET_SUITE = "tests/target"
REGRESSION_SUITE = "tests/regression"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_counts(stdout: str) -> dict[str, int]:
    """Extract pytest's summary counts from captured output."""

    def _first(pattern: re.Pattern[str]) -> int:
        match = pattern.search(stdout)
        return int(match.group(1)) if match else 0

    return {
        "passed": _first(_PASSED),
        "failed": _first(_FAILED),
        "errors": _first(_ERRORS),
        "skipped": _first(_SKIPPED),
    }


def run_pytest(
    work_dir: Path,
    suite: str,
    *,
    log_path: Path,
    python: str | None = None,
    extra_args: Sequence[str] = (),
    timeout_s: int = 900,
    code_version: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one pytest suite inside ``work_dir`` and write a JSON evidence log."""

    python_exe = str(python or os.environ.get("SDDCTX_PYTHON") or sys.executable)
    # ``demo.sh`` exports a repository-relative interpreter path; pytest runs
    # with cwd=<temporary project tree>, so make it absolute.  Use abspath (not
    # resolve) so a venv symlink is not replaced by the base interpreter, whose
    # site-packages do not contain the project dependencies.
    if not os.path.isabs(python_exe) and os.path.exists(python_exe):
        python_exe = os.path.abspath(python_exe)
    junit_path = log_path.with_suffix(".junit.xml")
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    # Absolute path: pytest runs with cwd=work_dir, so a relative --junitxml
    # would be written inside the (possibly temporary) project tree.
    junit_path = junit_path.resolve()
    command = [
        python_exe,
        "-m",
        "pytest",
        suite,
        "-q",
        "--tb=short",
        f"--junitxml={junit_path}",
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    started = _utc_now()
    t0 = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            command,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            check=False,
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = "TIMEOUT"
    elapsed = round(time.monotonic() - t0, 3)
    counts = parse_junit(junit_path) or parse_counts(stdout)
    testcase_ids = parse_junit_testcases(junit_path)

    record: dict[str, Any] = {
        "schema": "sddctx.test-log/v1",
        "suite": suite,
        "command": " ".join(command),
        "cwd": str(work_dir),
        "started_at": started,
        "duration_s": elapsed,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "counts": counts,
        "junit": str(junit_path),
        "testcase_ids": sorted(testcase_ids) if testcase_ids is not None else None,
        "testcase_count": len(testcase_ids) if testcase_ids is not None else None,
        "code_version": code_version or {},
        "stdout": stdout,
        "stdout_sha256": _sha256_text(stdout),
        "stderr": stderr,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    record["log_path"] = str(log_path)
    return record


def run_suites(
    demo_dir: Path,
    *,
    log_dir: Path,
    prefix: str,
    python: str | None = None,
    suites: Sequence[tuple[str, str]] = (("target", TARGET_SUITE), ("regression", REGRESSION_SUITE)),
    code_version: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run both suites and return ``{name: record}``."""

    results: dict[str, dict[str, Any]] = {}
    for name, suite in suites:
        results[name] = run_pytest(
            demo_dir,
            suite,
            log_path=log_dir / f"{prefix}-{name}.json",
            python=python,
            code_version=code_version,
        )
    return results


def copy_project(src: Path, dst: Path) -> Path:
    """Copy a business project tree without caches/venvs."""

    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".venv", "*.pyc")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)
    return dst


def _junit_root(xml_path: Path) -> Any | None:
    """Return the parsed XML root or ``None`` when missing/unparsable."""

    if not xml_path.is_file():
        return None
    import xml.etree.ElementTree as ET

    try:
        return ET.parse(str(xml_path)).getroot()
    except (ET.ParseError, OSError):
        return None


def parse_junit(xml_path: Path) -> dict[str, int] | None:
    """Parse a pytest JUnit XML report into pass/fail/error/skip counts."""

    root = _junit_root(xml_path)
    if root is None:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, "0") or 0)
    passed = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    return {
        "passed": max(passed, 0),
        "failed": totals["failures"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
    }


def parse_junit_testcases(xml_path: Path) -> set[str] | None:
    """Return ``{"classname::name", ...}`` for every ``<testcase>`` element.

    ``None`` means the JUnit XML is missing or unparsable (never confuse that
    with an empty test set).  This is the authoritative "same test files were
    executed" signal: it includes failed, errored and skipped tests, and is
    stable across before/after runs as long as the same tests were collected.
    """

    root = _junit_root(xml_path)
    if root is None:
        return None
    testcases: set[str] = set()
    for testcase in root.iter("testcase"):
        name = (testcase.get("name") or "").strip()
        if not name:
            continue
        classname = (testcase.get("classname") or "").strip()
        testcases.add(f"{classname}::{name}" if classname else name)
    return testcases


def record_testcases(record: dict[str, Any]) -> set[str] | None:
    """Extract the testcase set referenced by one :func:`run_pytest` record."""

    junit = record.get("junit")
    if not junit:
        return None
    return parse_junit_testcases(Path(str(junit)))


def make_before_tree(
    demo_dir: Path,
    patch_text: str,
    *,
    work_root: Path | None = None,
) -> dict[str, Any]:
    """Create a temp copy of the post-change tree with the change reverted.

    Returns a record with the temp path, the patch hash and the apply result so
    the caller can log exactly how the "before" state was produced.
    """

    root = Path(work_root) if work_root is not None else Path(tempfile.mkdtemp(prefix="sddctx-before-"))
    root.mkdir(parents=True, exist_ok=True)
    before_dir = copy_project(demo_dir, root / "demo")
    patch_file = root / "reverse.patch"
    patch_file.write_text(patch_text, encoding="utf-8")
    # Pure-Python engine: works on any OS, no `patch`/`git` binary required.
    result = reverse_patch(before_dir, patch_text)
    return {
        "before_dir": before_dir,
        "patch_path": patch_file,
        "patch_sha256": _sha256_text(patch_text),
        "apply_exit_code": 0 if result.get("ok") else 1,
        "apply_stderr": str(result.get("stderr") or ""),
        "apply_stdout": str(result.get("stdout") or ""),
        "apply_tool": result.get("tool"),
        "ok": bool(result.get("ok")),
    }


def evaluate(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    *,
    expected_target_failures_before: int | None = None,
) -> dict[str, Any]:
    """Decide whether the before/after transcripts satisfy the acceptance rules."""

    before_target = before["target"]
    before_regression = before["regression"]
    after_target = after["target"]
    after_regression = after["regression"]

    checks: list[dict[str, Any]] = []

    def _check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    target_failed_before = before_target["counts"]["failed"] + before_target["counts"]["errors"]
    _check(
        "target fails before the change",
        target_failed_before > 0 and before_target["exit_code"] != 0,
        f"failing target tests before = {target_failed_before}, exit={before_target['exit_code']}",
    )
    if expected_target_failures_before is not None:
        _check(
            "expected number of failing target tests",
            target_failed_before == expected_target_failures_before,
            f"expected {expected_target_failures_before}, observed {target_failed_before}",
        )
    _check(
        "regression passes before the change",
        before_regression["exit_code"] == 0 and before_regression["counts"]["failed"] == 0,
        f"exit={before_regression['exit_code']}, failed={before_regression['counts']['failed']}",
    )
    _check(
        "target passes after the change",
        after_target["exit_code"] == 0 and after_target["counts"]["failed"] == 0,
        f"exit={after_target['exit_code']}, passed={after_target['counts']['passed']}",
    )
    _check(
        "regression passes after the change",
        after_regression["exit_code"] == 0 and after_regression["counts"]["failed"] == 0,
        f"exit={after_regression['exit_code']}, passed={after_regression['counts']['passed']}",
    )

    # "Same test files were executed" is verified from the JUnit XML testcase
    # sets (classname::name), not from command strings/counts: a renamed,
    # removed or newly added test would otherwise slip through silently.
    same_tests_ok = True
    same_tests_details: list[str] = []
    for suite_name, before_record, after_record in (
        ("target", before_target, after_target),
        ("regression", before_regression, after_regression),
    ):
        before_ids = record_testcases(before_record)
        after_ids = record_testcases(after_record)
        if before_ids is None or after_ids is None:
            same_tests_ok = False
            same_tests_details.append(f"{suite_name}: JUnit XML missing or unparsable")
            continue
        if not before_ids:
            same_tests_ok = False
            same_tests_details.append(f"{suite_name}: JUnit XML contains no testcases")
            continue
        if before_ids != after_ids:
            same_tests_ok = False
            only_before = sorted(before_ids - after_ids)[:5]
            only_after = sorted(after_ids - before_ids)[:5]
            same_tests_details.append(
                f"{suite_name}: only-before={only_before} only-after={only_after}"
            )
        else:
            same_tests_details.append(f"{suite_name}: {len(after_ids)} identical testcases")
    _check(
        "same test files were executed",
        same_tests_ok,
        "; ".join(same_tests_details) or "no JUnit data",
    )
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
