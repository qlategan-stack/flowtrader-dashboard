"""
Local scheduled task — fetch Bybit testnet balance and commit it to the
dashboard repo so Streamlit Cloud can read journal/bybit_balance.json.

Why local: Bybit's testnet/demo endpoints are CloudFront geo-blocked from
US cloud datacenter IPs (Streamlit Cloud and likely GitHub Actions too).
A residential IP is not blocked, so we run from there and push the result.

Designed to run every 15 minutes via Windows Task Scheduler. Skips the
commit when balance fields are unchanged so we don't spam the git log.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT    = Path(__file__).resolve().parent.parent
BALANCE_FILE = REPO_ROOT / "journal" / "bybit_balance.json"
BALANCE_KEYS = ("total_usdt", "free_usdt", "open_positions", "positions")

sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from data.crypto_fetcher import BybitFetcher  # noqa: E402


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def _balance_unchanged(old: dict, new: dict) -> bool:
    return all(old.get(k) == new.get(k) for k in BALANCE_KEYS)


def main() -> int:
    bal = BybitFetcher().get_balance()
    if "error" in bal:
        print(f"[bybit-push] fetch error: {bal['error']}")
        return 1

    bal["fetched_at"] = datetime.now(timezone.utc).isoformat()

    if BALANCE_FILE.exists():
        try:
            old = json.loads(BALANCE_FILE.read_text(encoding="utf-8"))
            if _balance_unchanged(old, bal):
                print("[bybit-push] balance unchanged, skipping commit")
                return 0
        except Exception:
            pass

    BALANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BALANCE_FILE.write_text(json.dumps(bal, indent=2), encoding="utf-8")
    print(f"[bybit-push] wrote {BALANCE_FILE.name}: "
          f"USDT={bal['total_usdt']} positions={bal['open_positions']}")

    rel = "journal/bybit_balance.json"
    _git("add", rel)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        print("[bybit-push] git diff empty after add — nothing to commit")
        return 0

    msg = f"chore: bybit balance snapshot {bal['fetched_at']}"
    cm = _git("commit", "-m", msg)
    if cm.returncode != 0:
        print(f"[bybit-push] commit failed: {cm.stderr.strip()}")
        return 1
    push = _git("push")
    if push.returncode != 0:
        print(f"[bybit-push] push failed: {push.stderr.strip()}")
        return 1
    print("[bybit-push] pushed to origin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
