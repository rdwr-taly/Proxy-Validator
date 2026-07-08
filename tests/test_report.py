"""Focused tests for the SR3 report builder/writer (report.py)."""

from __future__ import annotations

import json
import os
import sys

# report.py lives at the repo root (same layout as main.py); make it importable.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from report import build_report, write_report  # noqa: E402


def _completed_results() -> dict:
    """A typical successful one-shot run: 800 in, 620 validated."""
    return {
        "proxies_tested": 800,
        "proxies_validated": 620,
        "proxies_failed": 180,
        "job_duration": 42.7,
        "job_success": True,
        "distribution_requested": True,
        "distribution_success": True,
    }


def test_build_report_measures_and_ratio() -> None:
    report = build_report(_completed_results())
    assert report["schema_version"] == 1
    assert report["status"] == "final"
    m = report["measures"]
    assert m["proxies_tested"] == 800
    assert m["proxies_validated"] == 620
    assert m["proxies_failed"] == 180
    assert m["validation_success_ratio"] == round(620 / 800, 4)  # 0.775
    assert m["job_success"] == "true"
    assert m["job_duration_seconds"] == 42.7
    assert m["distribution_success"] == "true"
    assert "620 of 800" in report["summary"]


def test_build_report_failed_run_serializes_false() -> None:
    report = build_report({"job_success": False, "proxies_tested": 500})
    m = report["measures"]
    assert m["job_success"] == "false"
    assert m["proxies_validated"] == 0
    # failed defaults to tested - validated when not given explicitly
    assert m["proxies_failed"] == 500
    assert m["validation_success_ratio"] == 0.0
    assert report["status"] == "final"


def test_build_report_no_distribution_is_first_class_na() -> None:
    # distribution_success is ALWAYS present (Task 11: first-class measure so
    # it can be checked by user-defined app alerts) — "n/a" when distribution
    # wasn't requested, rather than omitted from measures.
    report = build_report({"job_success": True, "proxies_tested": 10, "proxies_validated": 10})
    assert "distribution_success" in report["measures"]
    assert report["measures"]["distribution_success"] == "n/a"


def test_build_report_distribution_requested_but_failed() -> None:
    report = build_report(
        {
            "job_success": True,
            "proxies_tested": 10,
            "proxies_validated": 10,
            "distribution_requested": True,
            "distribution_success": False,
        }
    )
    assert report["measures"]["distribution_success"] == "false"


def test_build_report_empty_results_distribution_is_na() -> None:
    # No distribution_requested key at all (e.g. container stopped before
    # run_job set it) -> still always present, defaults to "n/a".
    report = build_report({})
    assert report["measures"]["distribution_success"] == "n/a"


def test_build_report_empty_results_is_conservative() -> None:
    report = build_report({})
    m = report["measures"]
    assert m["job_success"] == "false"
    assert m["validation_success_ratio"] == 0.0
    assert report["status"] == "final"


def test_write_report_atomic_and_final(tmp_path) -> None:
    target = tmp_path / "report" / "report.json"
    ok = write_report(_completed_results(), str(target))
    assert ok is True
    assert target.exists()
    # no leftover tmp file
    assert not (tmp_path / "report" / "report.json.tmp").exists()
    data = json.loads(target.read_text())
    assert data["status"] == "final"
    assert data["measures"]["proxies_validated"] == 620
    assert data["measures"]["job_success"] == "true"


def test_write_report_env_override(tmp_path, monkeypatch) -> None:
    target = tmp_path / "custom.json"
    monkeypatch.setenv("SR_REPORT_PATH", str(target))
    assert write_report(_completed_results()) is True
    assert target.exists()


def test_write_report_unwritable_path_degrades() -> None:
    # A path under a file (not a dir) can't be created -> returns False, no raise.
    assert write_report({"job_success": True}, "/dev/null/nope/report.json") is False
