import asyncio
import aiohttp
import os
import re
import socket
import time

# --- Configuration ---
TARGET_URL = os.environ.get("VALIDATION_TARGET_URL", "https://httpbin.org/ip")
CONNECT_TIMEOUT = int(os.environ.get("VALIDATION_TIMEOUT", 6))
CONCURRENCY = int(os.environ.get("VALIDATION_CONCURRENCY", 150))
INPUT_FILE_PATH = os.environ.get("VALIDATION_INPUT", "/app/output/HTTP.txt")
OUTPUT_FILE_PATH = os.environ.get("VALIDATION_OUTPUT", "/app/output/HTTP.txt")

# Ports that are almost certainly SOCKS, not HTTP
SOCKS_PORTS = {1080, 1081, 9050, 9150, 4145}

# Regex to confirm we got a real IP response (not an error page)
IP_PATTERN = re.compile(r'"origin"\s*:\s*"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')


def is_valid_proxy(line: str) -> bool:
    """Check host:port format."""
    parts = line.split(':')
    if len(parts) != 2:
        return False
    host, port = parts
    if not port.isdigit():
        return False
    p = int(port)
    return 1 <= p <= 65535 and '.' in host


async def test_proxy(proxy: str, session: aiohttp.ClientSession, sem: asyncio.Semaphore) -> tuple[str, float] | None:
    """
    Test a proxy by fetching TARGET_URL through it.
    Returns (proxy, latency) on success, None on failure.
    """
    host, port_str = proxy.split(':')
    port = int(port_str)
    if port in SOCKS_PORTS:
        return None

    proxy_url = f"http://{proxy}"
    timeout = aiohttp.ClientTimeout(total=CONNECT_TIMEOUT)

    try:
        async with sem:
            t0 = time.monotonic()
            async with session.get(TARGET_URL, proxy=proxy_url, timeout=timeout, ssl=False) as resp:
                elapsed = time.monotonic() - t0
                if resp.status < 200 or resp.status >= 300:
                    return None
                # Read body to confirm it's a real proxy response
                body = await resp.text(encoding='utf-8', errors='ignore')
                if IP_PATTERN.search(body):
                    return (proxy, elapsed)
                # If target is not httpbin, accept any 2xx
                if "httpbin" not in TARGET_URL:
                    return (proxy, elapsed)
                return None
    except (asyncio.TimeoutError, aiohttp.ClientError, OSError, Exception):
        return None


async def main():
    print(f"=== Proxy Validation ===")
    print(f"  Target:      {TARGET_URL}")
    print(f"  Timeout:     {CONNECT_TIMEOUT}s")
    print(f"  Concurrency: {CONCURRENCY}")
    print(f"  Input:       {INPUT_FILE_PATH}")
    print(f"  Output:      {OUTPUT_FILE_PATH}")
    print()

    # --- Read & deduplicate ---
    try:
        with open(INPUT_FILE_PATH, 'r') as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        print(f"ERROR: {INPUT_FILE_PATH} not found")
        return

    seen = set()
    proxies = []
    for line in raw_lines:
        p = line.strip()
        if p and is_valid_proxy(p) and p not in seen:
            seen.add(p)
            proxies.append(p)

    print(f"  Raw lines:   {len(raw_lines)}")
    print(f"  After dedup: {len(proxies)}")
    print()

    if not proxies:
        with open(OUTPUT_FILE_PATH, 'w') as f:
            pass
        print("No proxies to validate.")
        return

    # --- Validate concurrently ---
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(
        limit=0,
        force_close=True,
        family=socket.AF_INET,
        enable_cleanup_closed=True,
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [test_proxy(p, session, sem) for p in proxies]
        results = await asyncio.gather(*tasks)

    # --- Sort by latency (fastest first) ---
    validated = [r for r in results if r is not None]
    validated.sort(key=lambda x: x[1])

    print(f"\n=== Results ===")
    print(f"  Tested:    {len(proxies)}")
    print(f"  Passed:    {len(validated)}")
    if validated:
        print(f"  Fastest:   {validated[0][0]} ({validated[0][1]:.2f}s)")
        print(f"  Slowest:   {validated[-1][0]} ({validated[-1][1]:.2f}s)")
        median = validated[len(validated)//2]
        print(f"  Median:    {median[0]} ({median[1]:.2f}s)")

    # --- Write output sorted by speed ---
    with open(OUTPUT_FILE_PATH, 'w') as f:
        for proxy, _ in validated:
            f.write(f"{proxy}\n")

    print(f"\n  Saved {len(validated)} proxies to {OUTPUT_FILE_PATH}")
    print("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
