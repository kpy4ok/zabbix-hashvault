#!/usr/bin/env python3
"""
Zabbix monitoring script for HashVault mining pool API.

Usage:
  hashvault_zabbix.py <wallet_address> <metric> [pool_type]

Examples:
  hashvault_zabbix.py 42Gfm collective.hashRate
  hashvault_zabbix.py 42Gfm revenue.confirmedBalance
  hashvault_zabbix.py 42Gfm collective.avg24hashRate pplns

Metric paths:
  collective.*   - Pool mining stats (hashRate, avg1hashRate, avg3hashRate,
                   avg6hashRate, avg24hashRate, shareRate, lastShare,
                   roundHashes, totalHashes, validShares, invalidShares,
                   staleShares, foundBlocks, currentEffort)
  solo.*         - Solo mining stats (same keys as collective)
  revenue.*      - Payment/balance info (totalPaid, dailyPaid, lastWithdrawal,
                   dailyCredited, payoutThreshold, confirmedBalance,
                   totalPaymentsSent, totalRewardsCredited,
                   auxConfirmedBalance, auxTotalPaid, auxDailyPaid,
                   auxDailyCredited, auxTotalPaymentsSent)
  revenue.unconfirmedBalance.collective.total
  revenue.unconfirmedBalance.solo.total

  Special computed metrics:
  revenue.confirmedBalanceXMR      - Confirmed balance in XMR (divided by 1e12)
  revenue.totalPaidXMR             - Total paid in XMR
  revenue.payoutThresholdXMR       - Payout threshold in XMR
  revenue.dailyCreditedXMR         - Daily credited in XMR
  collective.staleSharesPct        - Stale shares as % of valid shares
  collective.invalidSharesPct      - Invalid shares as % of valid shares

Install:
  1. Copy to /etc/zabbix/externalscripts/ (or your ExternalScripts path)
  2. chmod +x hashvault_zabbix.py
  3. Import the template XML into Zabbix

Exit codes:
  0 - Success
  1 - Error (message printed to stderr)
"""

import json
import sys
import urllib.request
import urllib.error
import time
import os
import tempfile

API_BASE = "https://api.hashvault.pro/v3/monero/wallet"
CACHE_DIR = tempfile.gettempdir()
CACHE_TTL = 30  # seconds - avoid hammering the API when Zabbix polls many items

ATOMIC_UNITS = 1e12  # Monero atomic units per XMR


def get_cache_path(wallet: str) -> str:
    return os.path.join(CACHE_DIR, f"hashvault_{wallet}.json")


def fetch_stats(wallet: str, pool_type: str = "false") -> dict:
    """Fetch wallet stats from API, with file-based caching."""
    cache_path = get_cache_path(wallet)

    # Check cache
    try:
        if os.path.exists(cache_path):
            mtime = os.path.getmtime(cache_path)
            if time.time() - mtime < CACHE_TTL:
                with open(cache_path, "r") as f:
                    return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass  # Cache miss or corrupt, fetch fresh

    url = f"{API_BASE}/{wallet}/stats?poolType={pool_type}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Write cache
    try:
        with open(cache_path, "w") as f:
            json.dump(data, f)
    except OSError:
        pass  # Non-fatal

    return data


def resolve_metric(data: dict, metric: str):
    """
    Resolve a dotted metric path against the API response.
    Also handles computed/derived metrics.
    """
    # --- Computed metrics ---
    computed = {
        "revenue.confirmedBalanceXMR": lambda d: d["revenue"]["confirmedBalance"] / ATOMIC_UNITS,
        "revenue.totalPaidXMR": lambda d: d["revenue"]["totalPaid"] / ATOMIC_UNITS,
        "revenue.payoutThresholdXMR": lambda d: d["revenue"]["payoutThreshold"] / ATOMIC_UNITS,
        "revenue.dailyCreditedXMR": lambda d: d["revenue"]["dailyCredited"] / ATOMIC_UNITS,
        "revenue.dailyPaidXMR": lambda d: d["revenue"]["dailyPaid"] / ATOMIC_UNITS,
        "revenue.auxConfirmedBalanceXMR": lambda d: d["revenue"]["auxConfirmedBalance"] / ATOMIC_UNITS,
        "collective.staleSharesPct": lambda d: (
            (d["collective"]["staleShares"] / d["collective"]["validShares"] * 100)
            if d["collective"]["validShares"] > 0 else 0
        ),
        "collective.invalidSharesPct": lambda d: (
            (d["collective"]["invalidShares"] / d["collective"]["validShares"] * 100)
            if d["collective"]["validShares"] > 0 else 0
        ),
        "solo.staleSharesPct": lambda d: (
            (d["solo"]["staleShares"] / d["solo"]["validShares"] * 100)
            if d["solo"]["validShares"] > 0 else 0
        ),
        "solo.invalidSharesPct": lambda d: (
            (d["solo"]["invalidShares"] / d["solo"]["validShares"] * 100)
            if d["solo"]["validShares"] > 0 else 0
        ),
    }

    if metric in computed:
        return computed[metric](data)

    # --- Standard dotted-path resolution ---
    keys = metric.split(".")
    node = data
    for key in keys:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            print(f"Unknown metric: {metric}", file=sys.stderr)
            sys.exit(1)
    return node


def discovery(data: dict) -> str:
    """
    Zabbix LLD (Low-Level Discovery) output for workers/miners.
    Returns JSON in Zabbix discovery format.
    Not used for this endpoint but included as a placeholder
    if you extend to /wallet/{addr}/workers.
    """
    return json.dumps({"data": []})


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: hashvault_zabbix.py <wallet_address> <metric> [pool_type]",
            file=sys.stderr,
        )
        sys.exit(1)

    wallet = sys.argv[1]
    metric = sys.argv[2]
    pool_type = sys.argv[3] if len(sys.argv) > 3 else "false"

    data = fetch_stats(wallet, pool_type)
    value = resolve_metric(data, metric)

    # Format output: floats with precision, ints as-is
    if isinstance(value, float):
        print(f"{value:.8f}")
    else:
        print(value)


if __name__ == "__main__":
    main()
