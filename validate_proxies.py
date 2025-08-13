import asyncio
import aiohttp
import os
import time
from urllib.parse import urlparse

# --- Configuration ---
# Read configuration from environment variables or use defaults
TARGET_URL = os.environ.get("VALIDATION_TARGET_URL", "http://httpbin.org/ip") # Website to test against
CONNECT_TIMEOUT = int(os.environ.get("VALIDATION_TIMEOUT", 5)) # Timeout in seconds for connection and reading
CONCURRENCY = int(os.environ.get("VALIDATION_CONCURRENCY", 100)) # Max concurrent checks
INPUT_FILE_PATH = "/app/output/HTTP.txt" # Path inside the container where proXXy writes its output
OUTPUT_FILE_PATH = "/app/output/HTTP.txt" # Overwrite the original file with the validated list

# --- Define common SOCKS ports ---
# Add any other ports you want to explicitly exclude as likely SOCKS
COMMON_SOCKS_PORTS = {
    1080,  # Standard SOCKS port
    1081,  # Common alternative
    9050,  # Often used by Tor SOCKS proxy
    9150,  # Often used by Tor Browser SOCKS proxy
    # Add other ports from your list if they consistently fail HTTP checks
    # and are suspected SOCKS, e.g., 4145, 8118, etc. if you are sure.
}
print(f"Filtering out common SOCKS ports: {COMMON_SOCKS_PORTS}")

# --- Helper Function ---
def is_valid_proxy_format(proxy_str):
    """Basic check if the string looks like host:port"""
    if not isinstance(proxy_str, str):
        return False
    parts = proxy_str.strip().split(':')
    # Check if exactly two parts and the second part consists of digits
    return len(parts) == 2 and parts[1].isdigit()

# --- Core Validation Logic ---
async def test_proxy(proxy: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> str | None:
    """
    Tests a single proxy against the target URL.
    Filters out common SOCKS ports before attempting HTTP connection.
    Returns the proxy string if successful HTTP check, None otherwise.
    """
    proxy = proxy.strip() # Clean up whitespace first
    if not is_valid_proxy_format(proxy):
        # Don't print here, it might be noisy if the input file has junk
        # print(f"[-] Invalid proxy format skipped: {proxy}")
        return None

    # --- ADDED: SOCKS Port Check ---
    try:
        host, port_str = proxy.split(':', 1)
        port = int(port_str)
        if port in COMMON_SOCKS_PORTS:
            print(f"[-] SKIP SOCKS: {proxy:<25} | Port {port} is common for SOCKS")
            return None # Skip this proxy, likely SOCKS
    except ValueError:
        # This case should theoretically be caught by is_valid_proxy_format,
        # but it's good practice to handle potential errors during parsing.
        print(f"[!] ERROR parsing port: {proxy}")
        return None
    # --- END: SOCKS Port Check ---

    proxy_url = f"http://{proxy}" # Construct the URL for aiohttp
    try:
        async with semaphore: # Limit concurrency
            start_time = time.monotonic()
            # Proceed with HTTP check ONLY if it wasn't filtered out
            async with session.get(TARGET_URL, proxy=proxy_url, timeout=CONNECT_TIMEOUT) as response:
                elapsed = time.monotonic() - start_time
                if 200 <= response.status < 300:
                    print(f"[+] SUCCESS: {proxy:<25} | Status: {response.status} | Time: {elapsed:.2f}s")
                    return proxy # Return the validated proxy string
                else:
                    print(f"[-] FAIL HTTP: {proxy:<25} | Status: {response.status} | Time: {elapsed:.2f}s")
                    return None
    except asyncio.TimeoutError:
        print(f"[-] TIMEOUT:   {proxy:<25} | Timeout > {CONNECT_TIMEOUT}s")
        return None
    except aiohttp.ClientProxyConnectionError as e:
        # This error often indicates the proxy exists but refused the HTTP connection
        # (could be SOCKS, could be misconfigured HTTP, could be rate-limited)
        print(f"[-] PROXY REFUSED: {proxy:<25} | Likely not HTTP or bad config | {e}")
        return None
    except aiohttp.ClientConnectorError as e:
         # Often contains OS-level errors like "Cannot assign requested address" or "Connection refused" to the proxy itself
         print(f"[-] PROXY CONNECT ERR: {proxy:<25} | Cannot reach proxy | {e}")
         return None
    except aiohttp.ClientError as e:
        # Catch other potential aiohttp client errors (DNS resolution, SSL issues if target was HTTPS, etc.)
        print(f"[-] CLIENT ERROR: {proxy:<25} | Error: {type(e).__name__} | {e}")
        return None
    except Exception as e:
        # Catch any other unexpected errors
        print(f"[!] UNEXPECTED ERROR: {proxy:<25} | Error: {type(e).__name__} | {e}")
        return None

async def main():
    """
    Main function to read proxies, run tests concurrently, and save results.
    """
    print("--- Starting Secondary Proxy Validation (HTTP Only - Filtering SOCKS Ports) ---") # Updated title
    print(f"Target URL: {TARGET_URL}")
    print(f"Timeout: {CONNECT_TIMEOUT}s")
    print(f"Max Concurrency: {CONCURRENCY}")
    print(f"Input File: {INPUT_FILE_PATH}")
    print(f"Output File: {OUTPUT_FILE_PATH}")
    print(f"Excluding common SOCKS ports: {COMMON_SOCKS_PORTS}") # Reminder

    try:
        with open(INPUT_FILE_PATH, 'r') as f:
            # Apply basic format filter during read for efficiency
            proxies_to_test = [line.strip() for line in f if is_valid_proxy_format(line.strip())]
    except FileNotFoundError:
        print(f"Error: Input file '{INPUT_FILE_PATH}' not found. Did proXXy run correctly?")
        return # Exit if no input file

    if not proxies_to_test:
        print("Input file is empty or contains no valid proxy formats. No proxies to validate.")
        # Ensure the output file is empty if the input was empty/invalid
        with open(OUTPUT_FILE_PATH, 'w') as f:
            pass
        return

    print(f"Read {len(proxies_to_test)} potentially valid proxies from {INPUT_FILE_PATH}.")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    # Use a single session for connection pooling
    # Connector with limit_per_host=0 might help avoid issues if many proxies resolve to the same IP,
    # but default is usually fine. Force IPv4 DNS resolution can sometimes help stability.
    connector = aiohttp.TCPConnector(limit_per_host=0, force_close=True, family=socket.AF_INET)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [test_proxy(proxy, session, semaphore) for proxy in proxies_to_test]
        results = await asyncio.gather(*tasks) # Run all tests concurrently

    # Filter out the failed tests (None values)
    validated_proxies = [p for p in results if p is not None]

    print(f"--- Validation Complete ---")
    print(f"Successfully validated {len(validated_proxies)} HTTP proxies (after filtering {len(proxies_to_test) - len(validated_proxies)} invalid/SOCKS/failed).") # Updated summary

    # Write the validated proxies to the output file, overwriting it
    print(f"Saving validated proxies to {OUTPUT_FILE_PATH}...")
    with open(OUTPUT_FILE_PATH, 'w') as f:
        for proxy in validated_proxies:
            f.write(f"{proxy}\n")

    print("--- Secondary Validation Finished ---")

# Add socket import if not already present
import socket

if __name__ == "__main__":
    # Consider adding loop policy for Windows if needed, but usually not required on Linux
    # if os.name == 'nt':
    #    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())