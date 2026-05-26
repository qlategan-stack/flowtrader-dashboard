"""
Local scheduled task — fetch crypto exchange balance and commit it to the
dashboard repo so Streamlit Cloud can read journal/bybit_balance.json.

NAMING NOTE (L-1 audit 2026-05-26): the "bybit" in this script's name and
in the BALANCE_FILE path is legacy. The bot uses BinanceFetcher when
BINANCE_API_KEY is set in .env (current state) and falls back to Bybit
otherwise. The internal `exchange` field of the JSON reflects the actual
venue. The filenames are kept as-is to avoid breaking the Windows Task
Scheduler entry and the Streamlit Cloud dashboard's file reference.

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
# Keys compared to decide whether to make a NEW git commit (we still always
# rewrite fetched_at — H-4 audit 2026-05-26 found the dashboard was 21h stale
# because unchanged-balance cycles skipped both commit AND file rewrite).
BALANCE_KEYS = ("total_usdt", "free_usdt", "open_positions", "positions")
BOT_CONFIG   = REPO_ROOT.parent / "trading-bot" / "trading-bot" / "config.yaml"

sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env")

from data.crypto_fetcher import BybitFetcher, BinanceFetcher  # noqa: E402


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def _load_watchlist_base_coins() -> set:
    """Read crypto watchlist from the bot's config.yaml; return base-coin set.
    Empty set = unknown (don't filter — avoid dropping real positions when
    config can't be read)."""
    if not BOT_CONFIG.exists():
        return set()
    try:
        import yaml
        cfg = yaml.safe_load(BOT_CONFIG.read_text(encoding="utf-8"))
        pairs = (cfg.get("watchlist") or {}).get("crypto") or []
        return {p.split("/")[0].upper() for p in pairs if isinstance(p, str) and "/" in p}
    except Exception as e:
        print(f"[crypto-push] could not load watchlist coins: {e}")
        return set()


def _filter_to_watchlist(positions: list, watchlist_coins: set) -> list:
    """Drop any position whose base currency is not in the bot's traded
    watchlist. Filters out Binance testnet faucet dust (SHIB, PEPE, FLOKI,
    1000SATS, ...), fiat residue (TRY, ZAR, UAH, ...), and meme rewards
    that inflated 'open_positions' to 30 in the H-4 audit."""
    if not watchlist_coins:
        return positions or []
    return [
        p for p in (positions or [])
        if str(p.get("currency", "")).upper() in watchlist_coins
    ]


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

    # H-4: filter testnet dust/fiat/meme noise out of the positions list and
    # recompute the count so the dashboard reflects real holdings only.
    watchlist_coins = _load_watchlist_base_coins()
    raw_pos_count = len(bal.get("positions") or [])
    bal["positions"] = _filter_to_watchlist(bal.get("positions"), watchlist_coins)
    bal["open_positions"] = len(bal["positions"])
    if watchlist_coins and raw_pos_count > bal["open_positions"]:
        print(f"[{label}-push] filtered {raw_pos_count - bal['open_positions']} "
              f"non-watchlist position(s) (kept {bal['open_positions']})")

    # H-4: belt-and-suspenders on the label so the dashboard never shows a
    # contradiction with the actual fetcher (the file historically had
    # exchange='binance' while being read as bybit data).
    bal["exchange"] = label
    bal["fetched_at"] = datetime.now(timezone.utc).isoformat()

    # H-4: ALWAYS rewrite the file (so fetched_at advances even when balance is
    # unchanged); only skip the COMMIT to keep the git log clean.
    skip_commit = False
    if BALANCE_FILE.exists():
        try:
            old = json.loads(BALANCE_FILE.read_text(encoding="utf-8"))
            if _balance_unchanged(old, bal):
                skip_commit = True
        except Exception:
            pass

    BALANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BALANCE_FILE.write_text(json.dumps(bal, indent=2), encoding="utf-8")
    if skip_commit:
        print(f"[{label}-push] balance unchanged, refreshed fetched_at only (no commit)")
        return 0
    print(f"[{label}-push] wrote {BALANCE_FILE.name}: "
          f"USDT={bal['total_usdt']} positions={bal['open_positions']}")

    rel = "journal/bybit_balance.json"
    _git("add", rel)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        print(f"[{label}-push] git diff empty after add — nothing to commit")
        return 0

    msg = f"chore: {label} balance snapshot {bal['fetched_at']}"
    cm = _git("commit", "-m", msg)
    if cm.returncode != 0:
        print(f"[{label}-push] commit failed: {cm.stderr.strip()}")
        return 1
    push = _git("push")
    if push.returncode != 0:
        print(f"[{label}-push] push failed: {push.stderr.strip()}")
        return 1
    print(f"[{label}-push] pushed to origin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
