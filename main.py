"""ShowRunner-managed entry point for Proxy Validator job.

Reads config from /config/app.json instead of env vars, runs the validation
pipeline (proXXy → validate_proxies → optional distribution), and reports
job metrics before exiting.

Config file format:
{
    "validate_args": ["--validate"],
    "distribute_file": true,
    "remote_dest_path": "/home/shared/proxies/HTTP.txt",
    "distribution_config": [
        {"host": "10.0.0.1", "user": "radware", "password": "..."}
    ],
    "validation_target_url": "https://httpbin.org/ip",
    "validation_timeout": 6,
    "validation_concurrency": 200
}
"""

import json
import os
import signal
import subprocess
import sys
import time
import logging

from showrunner_sdk import config, metrics, health

from report import DEFAULT_STATS_PATH, load_validation_stats, write_report

logger = logging.getLogger("proxy-validator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ── SDK: Metrics (brief window for scraping after job completes) ──
proxies_tested = metrics.gauge("proxies_tested_total", "Total proxies tested in last run")
proxies_validated = metrics.gauge("proxies_validated_total", "Proxies that passed validation")
proxies_failed = metrics.gauge("proxies_failed_total", "Proxies that failed validation")
job_duration = metrics.gauge("job_duration_seconds", "Duration of last validation run")
job_success = metrics.gauge("job_success", "1 if last run succeeded, 0 if failed")
distribution_success = metrics.gauge("distribution_success", "1 if distribution succeeded")
metrics.set_app_info(name="proxy-validator", version="2.2.0-sr3")


def apply_config_to_env(cfg_data: dict) -> list[str]:
    """Map config fields to env vars expected by entrypoint.sh and scripts."""
    if cfg_data.get("distribute_file"):
        os.environ["DISTRIBUTE_FILE"] = "true"
    else:
        os.environ["DISTRIBUTE_FILE"] = "false"

    if cfg_data.get("remote_dest_path"):
        os.environ["REMOTE_DEST_PATH"] = cfg_data["remote_dest_path"]

    if cfg_data.get("distribution_config"):
        os.environ["DISTRIBUTION_CONFIG_JSON"] = json.dumps(cfg_data["distribution_config"])

    if cfg_data.get("validation_target_url"):
        os.environ["VALIDATION_TARGET_URL"] = cfg_data["validation_target_url"]

    if cfg_data.get("validation_timeout"):
        os.environ["VALIDATION_TIMEOUT"] = str(cfg_data["validation_timeout"])

    if cfg_data.get("validation_concurrency"):
        os.environ["VALIDATION_CONCURRENCY"] = str(cfg_data["validation_concurrency"])

    return cfg_data.get("validate_args", ["--validate"])


def count_lines(filepath: str) -> int:
    """Count lines in a file, return 0 if missing."""
    try:
        with open(filepath) as f:
            return sum(1 for line in f if line.strip())
    except FileNotFoundError:
        return 0


def run_job(cfg_data: dict, results: dict) -> bool:
    """Run the proxy validation pipeline.

    ``results`` is populated in-place with the job's final outcome so the SR3
    report writer can serialize it on ANY exit path (see ``main``). It is
    updated at the end of the run; if the container is stopped before that,
    ``results`` keeps its conservative defaults (job_success=False).
    """
    start = time.time()
    health.set_status("running")

    distribute = bool(cfg_data.get("distribute_file"))
    results["distribution_requested"] = distribute

    args = apply_config_to_env(cfg_data)

    # Fallback only: line count of the proXXy output file before the run. The
    # authoritative counts come from the validator's stats sidecar (below) —
    # the validator fetches extra CONNECT sources itself, so what it tested is
    # more than what proXXy wrote, and the file is empty on a fresh container.
    stats_path = os.environ.get("VALIDATION_STATS", DEFAULT_STATS_PATH)
    try:
        os.remove(stats_path)  # never report a previous run's numbers
    except OSError:
        pass
    input_count = count_lines("/app/output/HTTP.txt")

    # Run entrypoint.sh which handles the full pipeline
    logger.info("Starting proxy validation pipeline")
    result = subprocess.run(
        ["/usr/local/bin/entrypoint.sh"] + args,
        cwd="/app",
    )

    elapsed = time.time() - start
    job_duration.set(elapsed)

    # Prefer the validator's own counts; fall back to line counts of the output.
    stats = load_validation_stats(stats_path)
    if stats is not None:
        input_count = stats["tested"]
        output_count = stats["passed"]
    else:
        output_count = count_lines("/app/output/HTTP.txt")
        input_count = max(input_count, output_count)

    proxies_tested.set(input_count)
    proxies_validated.set(output_count)
    proxies_failed.set(max(0, input_count - output_count))

    succeeded = result.returncode == 0

    # Record final outcome for both Prometheus (Tier-0) and the SR3 report.
    results.update(
        {
            "proxies_tested": input_count,
            "proxies_validated": output_count,
            "proxies_failed": max(0, input_count - output_count),
            "job_duration": elapsed,
            "job_success": succeeded,
        }
    )

    if succeeded:
        job_success.set(1)
        distribution_success.set(1 if distribute else 0)
        results["distribution_success"] = bool(distribute)
        health.set_status("completed")
        logger.info(
            "Job completed: %d/%d proxies validated in %.1fs",
            output_count, input_count, elapsed,
        )
        return True
    else:
        job_success.set(0)
        results["distribution_success"] = False
        health.set_status("error", reason=f"exit code {result.returncode}")
        logger.error("Job failed with exit code %d after %.1fs", result.returncode, elapsed)
        return False


def _install_signal_handlers() -> None:
    """Turn SIGTERM/SIGINT into a normal exception so the ``finally`` runs.

    Without this, a container stop (SIGTERM) would kill the process outright and
    the SR3 report would never be written. Raising ``SystemExit`` unwinds the
    stack through ``main``'s ``try/finally``, writing whatever outcome we have.
    """

    def _handler(signum, _frame):
        logger.info("Received signal %d — writing report and exiting", signum)
        raise SystemExit(143 if signum == signal.SIGTERM else 130)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main() -> None:
    _install_signal_handlers()

    # Conservative default outcome; overwritten by run_job on completion and
    # written by the SR3 report writer on EVERY exit path (completion, stop,
    # error) via the finally below.
    results: dict = {"job_success": False}
    success = False

    cfg = config.load()

    # Start metrics server — stays alive briefly after job for ShowRunner to scrape
    metrics.start_server()

    try:
        if not cfg:
            # No config file — fall back to env vars (backward compat)
            logger.info("No config file found, using environment variables")
            cfg = {
                "distribute_file": os.environ.get("DISTRIBUTE_FILE", "").lower() in ("1", "true", "yes"),
                "remote_dest_path": os.environ.get("REMOTE_DEST_PATH", ""),
                "validation_target_url": os.environ.get("VALIDATION_TARGET_URL", "https://httpbin.org/ip"),
                "validation_timeout": int(os.environ.get("VALIDATION_TIMEOUT", "6")),
                "validation_concurrency": int(os.environ.get("VALIDATION_CONCURRENCY", "200")),
            }
            dist_json = os.environ.get("DISTRIBUTION_CONFIG_JSON", "[]")
            try:
                cfg["distribution_config"] = json.loads(dist_json)
            except json.JSONDecodeError:
                cfg["distribution_config"] = []

        success = run_job(cfg, results)

        # Keep metrics server alive briefly so ShowRunner can scrape final results
        logger.info("Holding metrics server for 30s for scraping...")
        time.sleep(30)
    finally:
        # SR3: emit /report/report.json on the completion path AND on stop/error.
        # Best-effort — never masks the job's own exit code.
        write_report(results)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
