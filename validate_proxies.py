import asyncio
import aiohttp
import os
import re
import socket
import time

# --- Configuration ---
TARGET_URL = os.environ.get("VALIDATION_TARGET_URL", "https://httpbin.org/ip")
CONNECT_TIMEOUT = int(os.environ.get("VALIDATION_TIMEOUT", 6))
CONCURRENCY = int(os.environ.get("VALIDATION_CONCURRENCY", 200))
INPUT_FILE_PATH = os.environ.get("VALIDATION_INPUT", "/app/output/HTTP.txt")
OUTPUT_FILE_PATH = os.environ.get("VALIDATION_OUTPUT", "/app/output/HTTP.txt")

# Extra sources of CONNECT-capable proxies to fetch directly
# These use host:port:country format and need parsing
EXTRA_CONNECT_SOURCES = [
    "https://raw.githubusercontent.com/zloi-user/hideip.me/main/connect.txt",
    "https://raw.githubusercontent.com/zloi-user/hideip.me/main/https.txt",
]

# Ports that are almost certainly SOCKS, not HTTP
SOCKS_PORTS = {1080, 1081, 9050, 9150, 4145}

# Regex to confirm we got a real IP response
IP_PATTERN = re.compile(r'"origin"\s*:\s*"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')


def parse_proxy_line(line: str) -> str | None:
    """Parse host:port from various formats (host:port, host:port:country, etc.)"""
    line = line.strip()
    if not line:
        return None
    parts = line.split(':')
    if len(parts) >= 2 and parts[1].isdigit():
        host = parts[0]
        port = int(parts[1])
        if 1 <= port <= 65535 and '.' in host:
            return f"{host}:{port}"
    return None


async def fetch_extra_sources(session: aiohttp.ClientSession) -> list[str]:
    """Fetch additional CONNECT-capable proxy lists not handled by proXXy."""
    extra = []
    for url in EXTRA_CONNECT_SOURCES:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    for line in text.splitlines():
                        proxy = parse_proxy_line(line)
                        if proxy:
                            extra.append(proxy)
                    print(f"  [+] Fetched {url.split('/')[-1]}: got {len(text.splitlines())} lines")
        except Exception as e:
            print(f"  [-] Failed {url.split('/')[-1]}: {e}")
    return extra


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
                body = await resp.text(encoding='utf-8', errors='ignore')
                if IP_PATTERN.search(body):
                    return (proxy, elapsed)
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

    # --- Read proXXy output ---
    proxies_from_file = []
    try:
        with open(INPUT_FILE_PATH, 'r') as f:
            for line in f:
                proxy = parse_proxy_line(line)
                if proxy:
                    proxies_from_file.append(proxy)
    except FileNotFoundError:
        print(f"WARNING: {INPUT_FILE_PATH} not found, will use extra sources only")

    print(f"  From proXXy: {len(proxies_from_file)}")

    # --- Fetch extra CONNECT sources directly ---
    print(f"  Fetching {len(EXTRA_CONNECT_SOURCES)} extra CONNECT sources...")
    connector = aiohttp.TCPConnector(limit=10, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as fetch_session:
        extra_proxies = await fetch_extra_sources(fetch_session)
    print(f"  From extras: {len(extra_proxies)}")

    # --- Merge and deduplicate ---
    seen = set()
    proxies = []
    for p in proxies_from_file + extra_proxies:
        if p not in seen:
            seen.add(p)
            proxies.append(p)

    print(f"  After dedup: {len(proxies)}")
    print()

    if not proxies:
        with open(OUTPUT_FILE_PATH, 'w') as f:
            pass
        print("No proxies to validate.")
        return

    # --- Validate concurrently ---
    sem = asyncio.Semaphore(CONCURRENCY)
    test_connector = aiohttp.TCPConnector(
        limit=0,
        force_close=True,
        family=socket.AF_INET,
        enable_cleanup_closed=True,
    )
    async with aiohttp.ClientSession(connector=test_connector) as session:
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
