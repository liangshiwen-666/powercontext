"""Offline tests for ``sddctx.verify`` (JUnit testcase-set comparison + before tree).

No seekdb / PowerContext / MiniMax API is involved: only pytest execution in a
temporary project and the pure-Python patch engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sddctx.verify import (
    evaluate,
    make_before_tree,
    parse_junit,
    parse_junit_testcases,
    record_testcases,
    run_pytest,
)

ORIGINAL = "def add(a, b):\n    return a - b\n"
PATCHED = "def add(a, b):\n    return a + b\n"

PROPER_DIFF = (
    "diff --git a/app/x.py b/app/x.py\n"
    "--- a/app/x.py\n"
    "+++ b/app/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n"
    "-    return a - b\n"
    "+    return a + b\n"
)


def write_junit(path: Path, cases: list[tuple[str, str]], *, failed: set[str] = frozenset()) -> Path:
    """Write a minimal pytest-style JUnit XML file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    testcases = []
    for classname, name in cases:
        failure = ""
        if name in failed:
            failure = '<failure message="boom">AssertionError</failure>'
        testcases.append(
            f'<testcase classname="{classname}" name="{name}" time="0.001">{failure}</testcase>'
        )
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites>'
        f'<testsuite name="pytest" tests="{len(cases)}" failures="{len(failed)}" errors="0" skipped="0">'
        + "".join(testcases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def make_record(junit_path: Path, *, exit_code: int, passed: int, failed: int = 0) -> dict:
    return {
        "command": f"python -m pytest suite -q --junitxml={junit_path}",
        "exit_code": exit_code,
        "counts": {"passed": passed, "failed": failed, "errors": 0, "skipped": 0},
        "junit": str(junit_path),
    }


def make_before_after(tmp_path: Path, *, target_before, target_after, regression_before, regression_after):
    before = {
        "target": make_record(
            write_junit(tmp_path / "before-target.xml", target_before),
            exit_code=1,
            passed=len(target_before) - 1,
            failed=1,
        ),
        "regression": make_record(
            write_junit(tmp_path / "before-regression.xml", regression_before), exit_code=0, passed=len(regression_before)
        ),
    }
    after = {
        "target": make_record(
            write_junit(tmp_path / "after-target.xml", target_after), exit_code=0, passed=len(target_after)
        ),
        "regression": make_record(
            write_junit(tmp_path / "after-regression.xml", regression_after), exit_code=0, passed=len(regression_after)
        ),
    }
    return before, after


TARGET = [("tests.target.test_orders", "test_filter_by_status"), ("tests.target.test_orders", "test_filters_compose")]
REGRESSION = [("tests.regression.test_orders", "test_default_pagination")]


# --------------------------------------------------------------------------
# JUnit parsing
# --------------------------------------------------------------------------
def test_parse_junit_testcases_ids(tmp_path: Path):
    path = write_junit(tmp_path / "j.xml", TARGET + REGRESSION, failed={"test_filters_compose"})
    cases = parse_junit_testcases(path)
    assert cases == {
        "tests.target.test_orders::test_filter_by_status",
        "tests.target.test_orders::test_filters_compose",
        "tests.regression.test_orders::test_default_pagination",
    }


def test_parse_junit_testcases_missing_or_broken(tmp_path: Path):
    assert parse_junit_testcases(tmp_path / "nope.xml") is None
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><not-closed>", encoding="utf-8")
    assert parse_junit_testcases(broken) is None


def test_parse_junit_testcases_without_classname(tmp_path: Path):
    path = tmp_path / "j.xml"
    path.write_text(
        '<?xml version="1.0"?><testsuite tests="1"><testcase name="test_plain" /></testsuite>',
        encoding="utf-8",
    )
    assert parse_junit_testcases(path) == {"test_plain"}


def test_parse_junit_counts(tmp_path: Path):
    path = write_junit(tmp_path / "j.xml", TARGET + REGRESSION, failed={"test_filters_compose"})
    counts = parse_junit(path)
    assert counts == {"passed": 2, "failed": 1, "errors": 0, "skipped": 0}
    assert parse_junit(tmp_path / "missing.xml") is None


def test_record_testcases_uses_junit_field(tmp_path: Path):
    path = write_junit(tmp_path / "j.xml", TARGET)
    record = {"junit": str(path)}
    assert record_testcases(record) == parse_junit_testcases(path)
    assert record_testcases({}) is None


# --------------------------------------------------------------------------
# evaluate(): same-testcase-set check
# --------------------------------------------------------------------------
def test_evaluate_passes_when_testcase_sets_identical(tmp_path: Path):
    before, after = make_before_after(
        tmp_path,
        target_before=TARGET,
        target_after=TARGET,
        regression_before=REGRESSION,
        regression_after=REGRESSION,
    )
    verdict = evaluate(before, after, expected_target_failures_before=1)
    assert verdict["ok"] is True, verdict
    same = next(check for check in verdict["checks"] if check["check"] == "same test files were executed")
    assert same["ok"] is True
    assert "identical testcases" in same["detail"]


def test_evaluate_fails_when_testcase_sets_differ(tmp_path: Path):
    biased_after = [TARGET[0], ("tests.target.test_orders", "test_renamed")]
    before, after = make_before_after(
        tmp_path,
        target_before=TARGET,
        target_after=biased_after,
        regression_before=REGRESSION,
        regression_after=REGRESSION,
    )
    verdict = evaluate(before, after)
    same = next(check for check in verdict["checks"] if check["check"] == "same test files were executed")
    assert same["ok"] is False
    assert "test_filters_compose" in same["detail"] or "test_renamed" in same["detail"]
    assert verdict["ok"] is False


def test_evaluate_rejects_equal_counts_but_different_tests(tmp_path: Path):
    """The old implementation compared only counts — renamed tests slipped by."""

    after = [TARGET[0], ("tests.target.test_orders", "test_other_name")]
    before, after_records = make_before_after(
        tmp_path,
        target_before=TARGET,
        target_after=after,
        regression_before=REGRESSION,
        regression_after=REGRESSION,
    )
    verdict = evaluate(before, after_records)
    same = next(check for check in verdict["checks"] if check["check"] == "same test files were executed")
    assert same["ok"] is False
    assert "only-before" in same["detail"] and "only-after" in same["detail"]


def test_evaluate_fails_when_junit_is_missing(tmp_path: Path):
    before, after = make_before_after(
        tmp_path,
        target_before=TARGET,
        target_after=TARGET,
        regression_before=REGRESSION,
        regression_after=REGRESSION,
    )
    after["target"]["junit"] = str(tmp_path / "deleted.xml")
    verdict = evaluate(before, after)
    same = next(check for check in verdict["checks"] if check["check"] == "same test files were executed")
    assert same["ok"] is False
    assert "missing or unparsable" in same["detail"]
    assert verdict["ok"] is False


# --------------------------------------------------------------------------
# make_before_tree(): pure-Python reverse, no external `patch`
# --------------------------------------------------------------------------
def test_make_before_tree_uses_pure_python(tmp_path: Path):
    demo = tmp_path / "demo"
    (demo / "app").mkdir(parents=True)
    (demo / "app" / "x.py").write_text(PATCHED, encoding="utf-8")

    result = make_before_tree(demo, PROPER_DIFF, work_root=tmp_path / "work")
    assert result["ok"] is True, result
    assert result["apply_tool"] == "python"
    assert result["apply_exit_code"] == 0
    before_file = Path(result["before_dir"]) / "app" / "x.py"
    assert before_file.read_text(encoding="utf-8") == ORIGINAL
    assert Path(result["patch_path"]).is_file()
    assert len(result["patch_sha256"]) == 64


def test_make_before_tree_reports_reverse_failure(tmp_path: Path):
    demo = tmp_path / "demo"
    (demo / "app").mkdir(parents=True)
    (demo / "app" / "x.py").write_text(ORIGINAL, encoding="utf-8")  # already "before"

    result = make_before_tree(demo, PROPER_DIFF, work_root=tmp_path / "work")
    assert result["ok"] is False
    assert result["apply_exit_code"] == 1
    assert "context mismatch" in result["apply_stderr"]


# --------------------------------------------------------------------------
# run_pytest(): JUnit path is absolute and testcase ids are recorded
# --------------------------------------------------------------------------
def test_run_pytest_records_absolute_junit_and_testcases(tmp_path: Path):
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_smoke.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    log_path = tmp_path / "logs" / "smoke.json"
    record = run_pytest(project, "tests", log_path=log_path, python=sys.executable)

    assert record["exit_code"] == 0
    assert record["counts"]["passed"] == 1
    assert Path(record["junit"]).is_absolute()
    assert Path(record["junit"]).is_file()
    assert record["testcase_ids"] and record["testcase_ids"][0].endswith("::test_ok")
    assert record["testcase_count"] == 1
    assert log_path.is_file()
