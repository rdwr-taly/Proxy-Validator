"""SR3 report writer — emit ``/report/report.json`` for ShowRunner to pull.

ShowRunner v3.0 pulls this file out of the container at window close (via the
Docker API) and projects its ``measures`` into the demo report + runbook. The
app declares this contract in its ``.showrunner/appspec.json`` ``sdk`` block, so
ShowRunner knows the path and what measures to expect.

This app is a ONE-SHOT JOB: it validates a proxy list, then self-exits. The
report is therefore very nearly a serialization of the job's final outcome
state (tested/validated/failed counts, success flag, duration) that ``run_job``
already computes — no new measurement is invented here.

Fully optional and non-fatal: if the path is not writable the run is unaffected
(ShowRunner simply degrades to Tier-0, i.e. Prometheus metrics + logs). The file
is written atomically (tmp + rename) with ``status: "final"`` so ShowRunner never
observes a half-written report.

NOTE (portal-side): because this is a JOB that self-exits on completion, the
ShowRunner orchestrator MUST pull this file on the JOB *completion* path, not
only on an explicit stop. As of the SR3 pilot the orchestrator pulls the report
on explicit stop; a completion-path hook (pull-on-exit-0 / pull-when-container-
exits) is still required for one-shot jobs like this one, otherwise a naturally
completed run's report is never collected.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_REPORT_PATH = "/report/report.json"
# Sidecar written by validate_proxies.py with the run's real tested/passed counts.
# Since the 2026-08-03 hotfix the validator fetches extra CONNECT sources itself,
# so "tested" can no longer be derived from the proXXy output file by a caller.
DEFAULT_STATS_PATH = "/app/output/validation_stats.json"


def load_validation_stats(path: str | None = None) -> dict[str, int] | None:
    """Read the validator's stats sidecar. Returns ``{"tested", "passed"}`` or
    ``None`` when the file is missing/malformed (caller falls back to line counts).
    """
    target = Path(path or os.getenv("VALIDATION_STATS", DEFAULT_STATS_PATH))
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        tested = int(data["tested"])
        passed = int(data["passed"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if tested < 0 or passed < 0 or passed > tested:
        return None
    return {"tested": tested, "passed": passed}


def _as_bool_str(value: Any) -> str:
    """Serialize a truthy/falsey outcome flag as the string "true"/"false".

    The ``job_success`` measure is declared as an ``enum`` with options
    ["true", "false"], so the portal check ``job_success == "true"`` compares
    against a stable string rather than a Python bool or 1/0.
    """
    return "true" if bool(value) else "false"


def build_report(results: dict[str, Any]) -> dict[str, Any]:
    """Build the SR3 report document from the job's final outcome ``results``.

    ``results`` is the dict populated by ``run_job`` (see ``main.py``):
        proxies_tested, proxies_validated, proxies_failed, job_success,
        job_duration, distribution_success, distribution_requested.
    Missing keys default to a conservative "job did not complete" shape, which
    is what gets written if the container is stopped mid-run.

    ``distribution_success`` is always present in the returned ``measures``
    (first-class, per Task 11 of the User-Defined App Alerts feature): it is
    ``"n/a"`` when ``distribution_requested`` is falsey, else ``"true"``/
    ``"false"`` from ``results["distribution_success"]``.
    """
    tested = int(results.get("proxies_tested", 0) or 0)
    validated = int(results.get("proxies_validated", 0) or 0)
    # Prefer the explicit failed count; fall back to tested-minus-validated.
    failed = int(results.get("proxies_failed", max(0, tested - validated)) or 0)
    ratio = round(validated / tested, 4) if tested else 0.0
    success = bool(results.get("job_success", False))
    duration = round(float(results.get("job_duration", 0.0) or 0.0), 1)

    # Distribution is optional. The measure is always present (first-class,
    # checkable by user-defined app alerts — Task 11) but its value reflects
    # whether the run actually asked to push the validated list to remote
    # servers: "n/a" when distribution wasn't requested, else "true"/"false".
    if results.get("distribution_requested"):
        distribution_value = _as_bool_str(results.get("distribution_success", False))
    else:
        distribution_value = "n/a"

    measures: dict[str, Any] = {
        "proxies_tested": tested,
        "proxies_validated": validated,
        "proxies_failed": failed,
        "validation_success_ratio": ratio,
        "job_success": _as_bool_str(success),
        "job_duration_seconds": duration,
        "distribution_success": distribution_value,
    }

    if not success:
        summary = (
            f"Validation job did not complete successfully "
            f"({validated}/{tested} proxies validated in {duration:.1f}s)."
        )
    else:
        summary = (
            f"Validated {validated} of {tested} proxies "
            f"({ratio:.0%} success) in {duration:.1f}s."
        )

    return {
        "schema_version": 1,
        "status": "final",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measures": measures,
        "summary": summary,
    }


def write_report(results: dict[str, Any], path: str | None = None) -> bool:
    """Atomically write the SR3 report. Returns True on success, never raises."""
    target = Path(path or os.getenv("SR_REPORT_PATH", DEFAULT_REPORT_PATH))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(build_report(results), indent=2), encoding="utf-8")
        tmp.replace(target)  # atomic rename on the same filesystem
        LOGGER.info("SR3 report written to %s", target)
        return True
    except Exception:  # pragma: no cover - degrade to Tier-0, never affect the run
        LOGGER.debug(
            "SR3 report write failed; ShowRunner will degrade to Tier-0", exc_info=True
        )
        return False


__all__ = ["build_report", "write_report", "load_validation_stats", "DEFAULT_REPORT_PATH", "DEFAULT_STATS_PATH"]
