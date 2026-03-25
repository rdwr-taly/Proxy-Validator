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
    "validation_target_url": "http://httpbin.org/ip",
    "validation_timeout": 5,
    "validation_concurrency": 100
}
"""

import json
import os
import subprocess
import sys
import time
import logging

from showrunner_sdk import config, metrics, health

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
metrics.set_app_info(name="proxy-validator", version="1.1.0")


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


def run_job(cfg_data: dict) -> bool:
    """Run the proxy validation pipeline."""
    start = time.time()
    health.set_status("running")

    args = apply_config_to_env(cfg_data)

    # Count input proxies (before validation)
    input_count = count_lines("/app/output/HTTP.txt")

    # Run entrypoint.sh which handles the full pipeline
    logger.info("Starting proxy validation pipeline")
    result = subprocess.run(
        ["/usr/local/bin/entrypoint.sh"] + args,
        cwd="/app",
    )

    elapsed = time.time() - start
    job_duration.set(elapsed)

    # Count output proxies (after validation)
    output_count = count_lines("/app/output/HTTP.txt")

    proxies_tested.set(input_count)
    proxies_validated.set(output_count)
    proxies_failed.set(max(0, input_count - output_count))

    if result.returncode == 0:
        job_success.set(1)
        distribution_success.set(1 if cfg_data.get("distribute_file") else 0)
        health.set_status("completed")
        logger.info(
            "Job completed: %d/%d proxies validated in %.1fs",
            output_count, input_count, elapsed,
        )
        return True
    else:
        job_success.set(0)
        health.set_status("error", reason=f"exit code {result.returncode}")
        logger.error("Job failed with exit code %d after %.1fs", result.returncode, elapsed)
        return False


def main() -> None:
    cfg = config.load()

    # Start metrics server — stays alive briefly after job for ShowRunner to scrape
    metrics.start_server()

    if not cfg:
        # No config file — fall back to env vars (backward compat)
        logger.info("No config file found, using environment variables")
        cfg = {
            "distribute_file": os.environ.get("DISTRIBUTE_FILE", "").lower() in ("1", "true", "yes"),
            "remote_dest_path": os.environ.get("REMOTE_DEST_PATH", ""),
            "validation_target_url": os.environ.get("VALIDATION_TARGET_URL", "http://httpbin.org/ip"),
            "validation_timeout": int(os.environ.get("VALIDATION_TIMEOUT", "5")),
            "validation_concurrency": int(os.environ.get("VALIDATION_CONCURRENCY", "100")),
        }
        dist_json = os.environ.get("DISTRIBUTION_CONFIG_JSON", "[]")
        try:
            cfg["distribution_config"] = json.loads(dist_json)
        except json.JSONDecodeError:
            cfg["distribution_config"] = []

    success = run_job(cfg)

    # Keep metrics server alive briefly so ShowRunner can scrape final results
    logger.info("Holding metrics server for 30s for scraping...")
    time.sleep(30)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
