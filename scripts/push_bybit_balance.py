"""
Local scheduled task — fetch crypto exchange balance and commit it to the
dashboard repo so Streamlit Cloud can read journal/bybit_balance.json.

Uses Binance when BINANCE_API_KEY is set in .env; falls back to Bybit.

Why local: Bybit/Binance testnet endpoints may be geo-blocked from US cloud
datacenters. A residential IP avoids this, so we run from here and push.

Designed to run every 15 minutes via Windows Task Scheduler. Skips the
commit when balance fields are unchanged so we don't spam the git log.
"""
import builtins
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# Monkey-patch print so every log line carries a UTC timestamp. The L-4 audit
# finding (2026-05-23) flagged this log as un-timestamped, making it impossible
# to tell from the log alone when each push happened.
_orig_print = builtins.print


def print(*args, **kwargs):  # noqa: A001 — intentional shadow of builtin
    _orig_print(f"[{_ts()}]", *args, **kwargs)


builtins.print = print

REPO_ROOT    = Path(__file__).resolve().parent.parent
BALANCE_FILE = REPO_ROOT / "journal" / "bybit_balance.json"
BALANCE_KEYS = ("total_usdt", "free_usdt", "open_positions", "positions")

sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env")

from data.crypto_fetcher import BybitFetcher, BinanceFetcher  # noqa: E402


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def _balance_unchanged(old: dict, new: dict) -> bool:
    return all(old.get(k) == new.get(k) for k in BALANCE_KEYS)


def main() -> int:
    if os.getenv("BINANCE_API_KEY"):
        fetcher = BinanceFetcher()
        label = "binance"
    else:
        fetcher = BybitFetcher()
        label = "bybit"

    bal = fetcher.get_balance()
    if "error" in bal:
        print(f"[{label}-push] fetch error: {bal['error']}")
        return 1

    bal["fetched_at"] = datetime.now(timezone.utc).isoformat()

    if BALANCE_FILE.exists():
        try:
            old = json.loads(BALANCE_FILE.read_text(encoding="utf-8"))
            if _balance_unchanged(old, bal):
                print("[crypto-push] balance unchanged, skipping commit")
                return 0
        except Exception:
            pass

    BALANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BALANCE_FILE.write_text(json.dumps(bal, indent=2), encoding="utf-8")
    print(f"[crypto-push] wrote {BALANCE_FILE.name}: "
          f"USDT={bal['total_usdt']} positions={bal['open_positions']}")

    rel = "journal/bybit_balance.json"
    _git("add", rel)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        print("[crypto-push] git diff empty after add — nothing to commit")
        return 0

    msg = f"chore: {label} balance snapshot {bal['fetched_at']}"
    cm = _git("commit", "-m", msg)
    if cm.returncode != 0:
        print(f"[crypto-push] commit failed: {cm.stderr.strip()}")
        return 1
    push = _git("push")
    if push.returncode != 0:
        print(f"[crypto-push] push failed: {push.stderr.strip()}")
        return 1
    print("[crypto-push] pushed to origin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
