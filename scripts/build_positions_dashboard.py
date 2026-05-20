"""
scripts/build_positions_dashboard.py
Fetches live Alpaca account state, enriches with journal data,
generates positions.html and pushes to GitHub Pages.

Run after each bot cycle (chain at the end of run_bot.bat after push_journal.py),
or via Windows Task Scheduler every 15 minutes independently.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, date as _date_cls
from pathlib import Path

REPO_ROOT    = Path(__file__).resolve().parent.parent
JOURNAL_FILE = REPO_ROOT / "journal" / "trades.jsonl"
BALANCE_JSON = REPO_ROOT / "journal" / "bybit_balance.json"
OUT_HTML     = REPO_ROOT / "positions.html"

sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

# Norton Antivirus TLS fix — must run before any HTTPS call (Alpaca, Binance).
import ssl as _ssl
import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context as _create_ctx

class _NortonAdapter(_HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        ctx = _create_ctx(); ctx.load_default_certs()
        ctx.verify_flags &= ~_ssl.VERIFY_X509_STRICT
        kw["ssl_context"] = ctx; super().init_poolmanager(*a, **kw)

_orig = _requests.Session.__init__
def _patched(self, *a, **kw):
    _orig(self, *a, **kw); self.mount("https://", _NortonAdapter())
_requests.Session.__init__ = _patched

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from data.fetcher import MarketDataFetcher

GH_USER = "qlategan-stack"


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def _ensure_gh_user() -> bool:
    r = subprocess.run(
        ["gh", "auth", "switch", "--user", GH_USER],
        capture_output=True, text=True
    )
    return r.returncode == 0


# ── Direct Alpaca REST (bypasses alpaca-py/httpx SSL issues on Windows) ──────

def _alpaca_direct() -> dict:
    """Fetch account + positions from Alpaca REST using requests with verify=False.
    Only used as a fallback when the alpaca-py TradingClient can't connect."""
    import requests, urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    key    = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    paper  = os.getenv("PAPER_TRADING", "true").lower() == "true"
    base   = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    hdrs   = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    if not key or not secret:
        return {"error": "No Alpaca credentials in .env"}

    try:
        acct = requests.get(f"{base}/v2/account",   headers=hdrs, verify=False, timeout=10).json()
        pos  = requests.get(f"{base}/v2/positions",  headers=hdrs, verify=False, timeout=10).json()
        if not isinstance(pos, list):
            pos = []
        portfolio = float(acct.get("equity", 0))
        last_eq   = float(acct.get("last_equity", portfolio))
        return {
            "portfolio_value": portfolio,
            "buying_power":    float(acct.get("buying_power", 0)),
            "cash":            float(acct.get("cash", 0)),
            "open_positions":  len(pos),
            "day_pl":          portfolio - last_eq,
            "positions": [
                {
                    "symbol":         p.get("symbol", ""),
                    "qty":            float(p.get("qty", 0)),
                    "avg_entry":      float(p.get("avg_entry_price", 0)),
                    "current_price":  float(p.get("current_price", 0)),
                    "unrealized_pl":  float(p.get("unrealized_pl", 0)),
                    "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
                }
                for p in pos
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _alpaca_history_direct() -> list[dict]:
    """Fetch 30-day portfolio history via requests with verify=False."""
    import requests, urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    key    = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    paper  = os.getenv("PAPER_TRADING", "true").lower() == "true"
    base   = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    hdrs   = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    try:
        r    = requests.get(f"{base}/v2/account/portfolio/history",
                            headers=hdrs, params={"period": "1M", "timeframe": "1D"},
                            verify=False, timeout=10)
        data = r.json()
        import pytz
        ts   = data.get("timestamp") or []
        pl   = data.get("profit_loss") or []
        eq   = data.get("equity") or []
        out  = []
        for i, t in enumerate(ts):
            if t is None:
                continue
            d = datetime.fromtimestamp(t, tz=pytz.timezone("America/New_York")).strftime("%Y-%m-%d")
            out.append({
                "date":        d,
                "profit_loss": float(pl[i]) if i < len(pl) and pl[i] is not None else 0.0,
                "equity":      float(eq[i]) if i < len(eq) and eq[i] is not None else None,
            })
        return out
    except Exception as e:
        print(f"[positions-dash] history fetch error: {e}")
        return []


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_journal() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    entries = []
    for line in JOURNAL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _enrich_positions(positions: list[dict], journal: list[dict]) -> list[dict]:
    """Add stop_loss, take_profit, signal_score, days_held from the journal BUY entry."""
    buy_by_sym: dict[str, dict] = {}
    for e in journal:
        if e.get("action") == "BUY" and e.get("execution_status") in (
            "FILLED", "SUBMITTED", "SIMULATED"
        ):
            sym = e.get("symbol")
            if not sym:
                continue
            ts = e.get("timestamp", "")
            if sym not in buy_by_sym or ts > buy_by_sym[sym].get("timestamp", ""):
                buy_by_sym[sym] = e

    today = _date_cls.today()
    enriched = []
    for p in positions:
        sym  = p.get("symbol", "")
        je   = buy_by_sym.get(sym, {})
        stop = float(je.get("stop_loss", 0) or 0)
        tgt  = float(je.get("take_profit", 0) or 0)
        score = je.get("signal_score", "—")

        days_held = "—"
        if je.get("date"):
            try:
                entry_date = _date_cls.fromisoformat(je["date"])
                days_held = (today - entry_date).days
            except ValueError:
                pass

        cur = float(p.get("current_price", 0))
        pct_to_stop   = ((cur - stop) / cur * 100) if stop and cur else None
        pct_to_target = ((tgt - cur) / cur * 100) if tgt and cur else None

        enriched.append({
            **p,
            "stop_loss":      stop,
            "take_profit":    tgt,
            "signal_score":   score,
            "days_held":      days_held,
            "pct_to_stop":    round(pct_to_stop, 2) if pct_to_stop is not None else None,
            "pct_to_target":  round(pct_to_target, 2) if pct_to_target is not None else None,
        })
    return enriched


def _bybit_current_price(symbol: str) -> float | None:
    """Fetch current spot price from Bybit public REST — no auth required."""
    import requests
    bybit_sym = symbol.replace("/", "")  # BTC/USDT → BTCUSDT
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "spot", "symbol": bybit_sym},
            timeout=5,
        )
        data = r.json()
        items = data.get("result", {}).get("list", [])
        if items:
            return float(items[0].get("lastPrice", 0))
    except Exception:
        pass
    return None


def _infer_crypto_positions(journal: list[dict]) -> list[dict]:
    """
    Reconstruct open crypto positions from the journal.
    A crypto position is open when its symbol has a BUY entry with no
    subsequent SELL entry. Enriches with a live Bybit price for current
    value and unrealised P&L.
    """
    is_crypto = lambda sym: sym and "/" in sym

    # Most recent BUY per crypto symbol
    buy_by_sym: dict[str, dict] = {}
    for e in journal:
        sym = e.get("symbol", "")
        if not is_crypto(sym):
            continue
        if e.get("action") == "BUY" and e.get("execution_status") in (
            "FILLED", "SUBMITTED", "SIMULATED"
        ):
            ts = e.get("timestamp", "")
            if sym not in buy_by_sym or ts > buy_by_sym[sym].get("timestamp", ""):
                buy_by_sym[sym] = e

    # Remove symbols that have been sold after the last BUY
    for e in journal:
        sym = e.get("symbol", "")
        if not is_crypto(sym) or sym not in buy_by_sym:
            continue
        if e.get("action") == "SELL" and e.get("execution_status") in (
            "FILLED", "SUBMITTED", "SIMULATED"
        ):
            if e.get("timestamp", "") > buy_by_sym[sym].get("timestamp", ""):
                del buy_by_sym[sym]

    if not buy_by_sym:
        return []

    today = _date_cls.today()
    positions = []
    for sym, je in buy_by_sym.items():
        entry_px  = float(je.get("entry_price", 0) or 0)
        qty       = float(je.get("quantity", 0) or 0)
        stop      = float(je.get("stop_loss", 0) or 0)
        tgt       = float(je.get("take_profit", 0) or 0)
        score     = je.get("signal_score", "—")

        days_held = "—"
        if je.get("date"):
            try:
                days_held = (today - _date_cls.fromisoformat(je["date"])).days
            except ValueError:
                pass

        cur = _bybit_current_price(sym) or entry_px
        pl_usd  = (cur - entry_px) * qty if entry_px and qty else 0.0
        pl_pct  = ((cur - entry_px) / entry_px) if entry_px else 0.0

        pct_to_stop   = ((cur - stop) / cur * 100) if stop and cur else None
        pct_to_target = ((tgt - cur) / cur * 100) if tgt and cur else None

        positions.append({
            "symbol":         sym,
            "venue":          "CRYPTO",
            "qty":            qty,
            "avg_entry":      entry_px,
            "current_price":  cur,
            "unrealized_pl":  round(pl_usd, 4),
            "unrealized_plpc": round(pl_pct, 6),
            "stop_loss":      stop,
            "take_profit":    tgt,
            "signal_score":   score,
            "days_held":      days_held,
            "pct_to_stop":    round(pct_to_stop, 2) if pct_to_stop is not None else None,
            "pct_to_target":  round(pct_to_target, 2) if pct_to_target is not None else None,
        })
    return positions


def _load_crypto_balance() -> dict:
    if BALANCE_JSON.exists():
        try:
            d = json.loads(BALANCE_JSON.read_text(encoding="utf-8"))
            if "error" not in d:
                return d
        except Exception:
            pass
    return {}


def _fetch_portfolio_history(fetcher: MarketDataFetcher) -> list[dict]:
    try:
        result = fetcher.get_portfolio_history(period="1M", timeframe="1D")
        return result.get("history", [])
    except Exception:
        return []


# ── HTML generation ───────────────────────────────────────────────────────────

_SYMBOL_NAMES = {
    "NVDA": "NVIDIA", "AAPL": "Apple", "MSFT": "Microsoft",
    "AMD": "AMD", "TSLA": "Tesla", "META": "Meta",
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq-100 ETF",
    "IWM": "Russell 2000 ETF", "GLD": "Gold ETF",
    "SLV": "Silver ETF", "USO": "Oil ETF", "TLT": "T-Bond ETF",
    "GOOGL": "Alphabet", "AMZN": "Amazon", "JPM": "JPMorgan",
    "COIN": "Coinbase", "MSTR": "MicroStrategy",
    "RIOT": "Riot Platforms", "XOM": "ExxonMobil", "CVX": "Chevron",
}


def _fmt_pct(v, show_plus=True) -> str:
    if v is None:
        return "—"
    s = f"{v:+.2f}%" if show_plus else f"{v:.2f}%"
    return s


def _price_str(v: float) -> str:
    """Format a price — 4 decimal places for crypto (low unit price), 2 for equities."""
    if v == 0:
        return "—"
    return f"${v:,.4f}" if v < 10 else f"${v:,.2f}"


def _pos_row(p: dict) -> str:
    sym        = p.get("symbol", "")
    venue      = p.get("venue", "ALPACA")   # "ALPACA" or "CRYPTO"
    is_crypto  = venue == "CRYPTO"
    full_name  = _SYMBOL_NAMES.get(sym, "Bybit" if is_crypto else "")
    qty        = p.get("qty", 0)
    entry      = float(p.get("avg_entry", 0))
    cur        = float(p.get("current_price", 0))
    pl_usd     = float(p.get("unrealized_pl", 0))
    # Alpaca returns plpc as a fraction (0.021 = 2.1%); crypto is already a fraction too
    pl_pct     = float(p.get("unrealized_plpc", 0)) * 100
    stop       = float(p.get("stop_loss", 0) or 0)
    tgt        = float(p.get("take_profit", 0) or 0)
    score      = p.get("signal_score", "—")
    days       = p.get("days_held", "—")
    to_stop    = p.get("pct_to_stop")
    to_tgt     = p.get("pct_to_target")

    pl_cls  = "pos" if pl_usd >= 0 else "neg"
    pl_sign = "▲" if pl_usd >= 0 else "▼"

    stop_cls = ""
    if to_stop is not None and to_stop < 2:
        stop_cls = ' class="danger-cell"'

    stop_str  = _price_str(stop) if stop else "—"
    tgt_str   = _price_str(tgt)  if tgt  else "—"
    to_stop_s = _fmt_pct(to_stop) if to_stop is not None else "—"
    to_tgt_s  = _fmt_pct(to_tgt, show_plus=True) if to_tgt is not None else "—"
    venue_badge = '<span class="venue-crypto">CRYPTO</span>' if is_crypto else ""

    return f"""
      <tr>
        <td><span class="sym">{sym}</span><span class="sym-name">{full_name}{venue_badge}</span></td>
        <td>{qty:g}</td>
        <td>{_price_str(entry)}</td>
        <td>{_price_str(cur)}</td>
        <td class="{pl_cls}">{pl_sign} ${abs(pl_usd):,.4f}</td>
        <td class="{pl_cls}">{pl_pct:+.2f}%</td>
        <td{stop_cls}>{stop_str}<span class="cell-sub">{to_stop_s}</span></td>
        <td>{tgt_str}<span class="cell-sub">{to_tgt_s}</span></td>
        <td><span class="score-badge">{score}/6</span></td>
        <td>{days}d</td>
      </tr>"""


def build_html(
    account: dict,
    positions_enriched: list[dict],
    crypto: dict,
    history: list[dict],
    built_at: datetime,
) -> str:
    paper_mode   = os.getenv("PAPER_TRADING", "true").lower() == "true"
    mode_label   = "PAPER" if paper_mode else "LIVE"
    mode_cls     = "badge-paper" if paper_mode else "badge-live"

    portfolio    = float(account.get("portfolio_value", 0))
    day_pl       = float(account.get("day_pl", 0))
    cash         = float(account.get("cash", 0))
    bp           = float(account.get("buying_power", 0))
    open_pos     = len(positions_enriched)

    pl_sign      = "▲" if day_pl >= 0 else "▼"
    pl_cls       = "pos" if day_pl >= 0 else "neg"
    pl_pct       = day_pl / portfolio * 100 if portfolio else 0

    built_str    = built_at.strftime("%Y-%m-%d %H:%M UTC")

    # Crypto summary
    cx_usdt      = float(crypto.get("total_usdt", 0))
    cx_free      = float(crypto.get("free_usdt", 0))
    cx_pos_val   = float(crypto.get("position_value", 0))
    cx_positions = crypto.get("positions", [])
    cx_label     = crypto.get("exchange", "Bybit").capitalize()
    cx_fetched   = crypto.get("fetched_at", "")

    # Build crypto rows HTML
    cx_rows_html = ""
    if cx_positions:
        for p in sorted(cx_positions, key=lambda x: float(x.get("value_usd") or 0), reverse=True):
            currency  = p.get("currency", "")
            amount    = float(p.get("amount", 0))
            price     = float(p.get("price_usd") or 0)
            value     = float(p.get("value_usd") or 0)
            cx_rows_html += f"""
      <tr>
        <td><span class="sym">{currency}</span></td>
        <td>{amount:g}</td>
        <td>${price:,.4f}</td>
        <td>${value:,.2f}</td>
      </tr>"""
    else:
        cx_rows_html = '<tr><td colspan="4" class="empty-row">No crypto positions — balance file missing or exchange not connected</td></tr>'

    # Chart data
    chart_dates  = json.dumps([h["date"] for h in history])
    chart_equity = json.dumps([h.get("equity") for h in history])
    chart_pl     = json.dumps([round(h.get("profit_loss", 0), 2) for h in history])

    # Position rows HTML
    pos_rows_html = "".join(_pos_row(p) for p in positions_enriched)
    if not pos_rows_html:
        pos_rows_html = '<tr><td colspan="10" class="empty-row">No open positions</td></tr>'

    # Loss limit
    max_loss_pct = 0.02
    max_loss     = portfolio * max_loss_pct
    loss_used    = abs(day_pl) if day_pl < 0 else 0
    loss_frac    = min(loss_used / max_loss, 1.0) if max_loss else 0
    loss_icon    = "🔴" if loss_frac >= 0.8 else "🟡" if loss_frac >= 0.5 else "🟢"

    max_positions = 3
    cap_frac     = open_pos / max_positions
    cap_icon     = "🔴" if cap_frac >= 1.0 else "🟡" if cap_frac >= 0.67 else "🟢"

    return f"""<!DOCTYPE html>
<html lang="en" class="theme-dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="1800">
<title>FlowTrader — Positions</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@300;400;700;800;900&family=Barlow:wght@300;400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>var t=localStorage.getItem('ft-theme');if(t)document.documentElement.className=t;</script>
<style>
/* ── RAW TOKENS ────────────────────────────────────────────────────────────── */
:root {{
  --_y50:#FEF9E0;--_y100:#FDF0A0;--_y200:#FAE04D;
  --_y400:#F5C400;--_y600:#D4A800;--_y800:#A88000;--_y900:#6A5000;
  --_n50:#E8EFF8;--_n100:#B8CCE8;--_n300:#6B9ED0;
  --_n500:#2D6BA8;--_n700:#1A3D6E;--_n900:#0D2040;--_n950:#071022;
  --_g0:#FFFFFF;--_g50:#F7F6F3;--_g100:#E8E7E2;--_g200:#C8C7C0;
  --_g400:#949390;--_g600:#5C5B58;--_g800:#2E2E2C;
  --_g900:#1A1A18;--_g950:#0D0D0B;
  --_teal:#2D8C7A;--_teal-light:#C8EDE7;--_teal-dark:#1a5c50;
  --_coral:#E86060;--_coral-light:#FDDCDC;
  --font-display:'Barlow Condensed',sans-serif;
  --font-body:'Barlow',sans-serif;
  --r-sm:4px;--r-md:8px;--r-lg:12px;--r-xl:16px;
}}
.theme-dark {{
  color-scheme:dark;
  --color-surface-page:var(--_g950);--color-surface-base:var(--_g900);
  --color-surface-elevated:var(--_g800);--color-surface-sunken:var(--_g950);
  --color-text-primary:var(--_g100);--color-text-secondary:var(--_g400);
  --color-text-tertiary:var(--_g600);--color-text-on-brand:var(--_g950);
  --color-brand-primary:var(--_y400);--color-brand-hover:var(--_y200);
  --color-border-subtle:rgba(255,255,255,0.06);
  --color-border-default:rgba(255,255,255,0.10);
  --color-border-strong:rgba(255,255,255,0.20);
  --color-success-bg:rgba(45,140,122,0.12);--color-success-fg:var(--_teal-light);
  --color-danger-bg:rgba(232,96,96,0.12);--color-danger-fg:var(--_coral-light);
  --shadow-md:0 4px 12px rgba(0,0,0,0.40);
}}
.theme-light {{
  color-scheme:light;
  --color-surface-page:var(--_g50);--color-surface-base:var(--_g0);
  --color-surface-elevated:var(--_g0);--color-surface-sunken:var(--_g100);
  --color-text-primary:var(--_g950);--color-text-secondary:var(--_g600);
  --color-text-tertiary:var(--_g400);--color-text-on-brand:var(--_g950);
  --color-brand-primary:var(--_y400);--color-brand-hover:var(--_y600);
  --color-border-subtle:var(--_g100);--color-border-default:var(--_g200);
  --color-border-strong:var(--_g400);
  --color-success-bg:#EDF7F5;--color-success-fg:var(--_teal-dark);
  --color-danger-bg:#FEF2F2;--color-danger-fg:#C0392B;
  --shadow-md:0 4px 12px rgba(0,0,0,0.08);
}}
.theme-navy {{
  color-scheme:dark;
  --color-surface-page:var(--_n950);--color-surface-base:var(--_n900);
  --color-surface-elevated:var(--_n700);--color-surface-sunken:var(--_n950);
  --color-text-primary:var(--_g0);--color-text-secondary:var(--_n100);
  --color-text-tertiary:var(--_n300);--color-text-on-brand:var(--_g950);
  --color-brand-primary:var(--_y400);--color-brand-hover:var(--_y200);
  --color-border-subtle:rgba(107,158,208,0.12);
  --color-border-default:rgba(107,158,208,0.20);
  --color-border-strong:rgba(107,158,208,0.35);
  --color-success-bg:rgba(45,140,122,0.15);--color-success-fg:var(--_teal-light);
  --color-danger-bg:rgba(232,96,96,0.14);--color-danger-fg:var(--_coral-light);
  --shadow-md:0 4px 12px rgba(0,0,0,0.50);
}}

/* ── BASE ──────────────────────────────────────────────────────────────────── */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;background:var(--color-surface-page);color:var(--color-text-primary);
  font-family:var(--font-body);font-size:14px;line-height:1.5}}

/* ── HEADER ────────────────────────────────────────────────────────────────── */
.hdr{{
  display:flex;align-items:center;gap:16px;
  padding:12px 24px;
  background:var(--color-surface-base);
  border-bottom:1px solid var(--color-border-default);
  flex-wrap:wrap;
}}
.hdr-brand{{
  font-family:var(--font-display);font-weight:900;font-size:22px;
  letter-spacing:0.02em;color:var(--color-brand-primary);
  display:flex;align-items:center;gap:8px;
}}
.badge-paper{{
  background:rgba(245,196,0,0.15);color:var(--_y400);
  border:1px solid rgba(245,196,0,0.35);
  font-family:var(--font-display);font-weight:700;font-size:11px;
  letter-spacing:0.12em;padding:2px 10px;border-radius:50px;
}}
.badge-live{{
  background:rgba(232,96,96,0.15);color:var(--_coral);
  border:1px solid rgba(232,96,96,0.35);
  font-family:var(--font-display);font-weight:700;font-size:11px;
  letter-spacing:0.12em;padding:2px 10px;border-radius:50px;
}}
.hdr-pv{{
  font-family:var(--font-display);font-weight:900;font-size:28px;
  color:var(--color-text-primary);margin-left:auto;
}}
.hdr-pl{{font-family:var(--font-display);font-weight:700;font-size:20px;}}
.hdr-ts{{
  font-size:11px;color:var(--color-text-tertiary);white-space:nowrap;
  margin-left:8px;
}}

/* ── THEME BAR ─────────────────────────────────────────────────────────────── */
.theme-bar{{
  display:flex;gap:4px;padding:6px 24px;
  background:var(--color-surface-elevated);
  border-bottom:1px solid var(--color-border-subtle);
}}
.theme-bar button{{
  font-family:var(--font-display);font-weight:700;font-size:11px;
  letter-spacing:0.08em;text-transform:uppercase;
  padding:4px 14px;border-radius:50px;border:1px solid var(--color-border-default);
  background:transparent;color:var(--color-text-secondary);cursor:pointer;
  transition:all 0.15s;
}}
.theme-bar button:hover{{border-color:var(--color-brand-primary);color:var(--color-brand-primary)}}
.theme-bar button.active{{
  background:var(--color-brand-primary);color:var(--color-text-on-brand);
  border-color:var(--color-brand-primary);
}}

/* ── LAYOUT ────────────────────────────────────────────────────────────────── */
.page{{max-width:1400px;margin:0 auto;padding:24px}}
.section-title{{
  font-family:var(--font-display);font-weight:800;font-size:13px;
  letter-spacing:0.12em;text-transform:uppercase;
  color:var(--color-text-tertiary);margin-bottom:12px;margin-top:28px;
}}

/* ── STAT CARDS ────────────────────────────────────────────────────────────── */
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:8px}}
.card{{
  background:var(--color-surface-base);border:1px solid var(--color-border-subtle);
  border-radius:var(--r-lg);padding:16px 20px;box-shadow:var(--shadow-md);
}}
.card-label{{
  font-family:var(--font-body);font-weight:500;font-size:11px;
  letter-spacing:0.08em;text-transform:uppercase;
  color:var(--color-text-tertiary);margin-bottom:4px;
}}
.card-value{{
  font-family:var(--font-display);font-weight:900;font-size:28px;
  color:var(--color-text-primary);line-height:1;
}}
.card-sub{{
  font-size:12px;color:var(--color-text-secondary);margin-top:4px;
}}

/* ── POSITIONS TABLE ───────────────────────────────────────────────────────── */
.tbl-wrap{{
  overflow-x:auto;
  background:var(--color-surface-base);
  border:1px solid var(--color-border-subtle);
  border-radius:var(--r-lg);
  box-shadow:var(--shadow-md);
}}
table{{width:100%;border-collapse:collapse}}
thead tr{{
  background:var(--color-surface-elevated);
  border-bottom:1px solid var(--color-border-default);
}}
th{{
  font-family:var(--font-display);font-weight:700;font-size:11px;
  letter-spacing:0.08em;text-transform:uppercase;
  color:var(--color-text-tertiary);padding:12px 16px;
  text-align:left;white-space:nowrap;
}}
td{{
  padding:14px 16px;border-bottom:1px solid var(--color-border-subtle);
  vertical-align:middle;white-space:nowrap;
}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:rgba(255,255,255,0.03)}}
.sym{{
  font-family:var(--font-display);font-weight:800;font-size:16px;
  display:block;
}}
.sym-name{{
  font-size:11px;color:var(--color-text-tertiary);display:block;margin-top:1px;
}}
.pos{{color:#2D8C7A}}
.neg{{color:#E86060}}
.cell-sub{{
  display:block;font-size:11px;color:var(--color-text-tertiary);margin-top:2px;
}}
.danger-cell{{color:#E86060 !important;font-weight:600}}
.score-badge{{
  font-family:var(--font-display);font-weight:700;font-size:13px;
  background:rgba(245,196,0,0.12);color:var(--_y400);
  border:1px solid rgba(245,196,0,0.25);
  padding:2px 10px;border-radius:50px;
}}
.venue-crypto{{
  display:inline-block;margin-left:6px;
  font-family:var(--font-display);font-weight:700;font-size:10px;
  letter-spacing:0.1em;
  background:rgba(45,140,122,0.15);color:var(--_teal-light);
  border:1px solid rgba(45,140,122,0.30);
  padding:1px 7px;border-radius:50px;vertical-align:middle;
}}
.empty-row{{
  text-align:center;padding:40px;
  color:var(--color-text-tertiary);font-style:italic;
}}

/* ── GAUGE ─────────────────────────────────────────────────────────────────── */
.gauges{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}}
.gauge{{
  background:var(--color-surface-base);border:1px solid var(--color-border-subtle);
  border-radius:var(--r-lg);padding:16px 20px;
}}
.gauge-label{{
  font-size:12px;font-weight:500;color:var(--color-text-secondary);margin-bottom:8px;
}}
.gauge-track{{
  height:8px;background:var(--color-border-subtle);border-radius:50px;overflow:hidden;
}}
.gauge-fill{{height:100%;border-radius:50px;transition:width 0.4s}}
.gauge-sub{{font-size:11px;color:var(--color-text-tertiary);margin-top:6px}}

/* ── CHART ─────────────────────────────────────────────────────────────────── */
.chart-card{{
  background:var(--color-surface-base);border:1px solid var(--color-border-subtle);
  border-radius:var(--r-lg);padding:20px;box-shadow:var(--shadow-md);
  margin-top:0;
}}
.chart-wrap{{height:220px;position:relative}}

/* ── CRYPTO SECTION ────────────────────────────────────────────────────────── */
.cx-summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:16px}}

/* ── FOOTER ────────────────────────────────────────────────────────────────── */
.footer{{
  margin-top:40px;padding-top:16px;
  border-top:1px solid var(--color-border-subtle);
  font-size:11px;color:var(--color-text-tertiary);
  display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;
}}
</style>
</head>
<body>

<!-- ── HEADER ─────────────────────────────────────────────────────────────── -->
<div class="hdr">
  <div class="hdr-brand">⚡ FlowTrader</div>
  <span class="{mode_cls}">{mode_label}</span>
  <div class="hdr-pv">${portfolio:,.2f}</div>
  <div class="hdr-pl {pl_cls}">{pl_sign} ${abs(day_pl):,.2f} ({pl_pct:+.2f}%)</div>
  <div class="hdr-ts">Built {built_str} · auto-refreshes every 30 min</div>
</div>

<!-- ── THEME TOGGLE ────────────────────────────────────────────────────────── -->
<div class="theme-bar">
  <button onclick="ftTheme('theme-light',this)">Light</button>
  <button onclick="ftTheme('theme-dark',this)" class="active">Dark</button>
  <button onclick="ftTheme('theme-navy',this)">Navy</button>
</div>

<div class="page">

  <!-- ── ACTIVE POSITIONS ──────────────────────────────────────────────────── -->
  <div class="section-title">Active Positions ({open_pos})</div>
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Qty</th>
          <th>Entry</th>
          <th>Current</th>
          <th>P&amp;L $</th>
          <th>P&amp;L %</th>
          <th>Stop ↓ dist</th>
          <th>Target ↑ dist</th>
          <th>Score</th>
          <th>Days</th>
        </tr>
      </thead>
      <tbody>
        {pos_rows_html}
      </tbody>
    </table>
  </div>

  <!-- ── EQUITY CHART ───────────────────────────────────────────────────────── -->
  <div class="section-title" style="margin-top:32px">Portfolio Equity — 30 Days</div>
  <div class="chart-card">
    <div class="chart-wrap">
      <canvas id="equityChart"></canvas>
    </div>
  </div>

  <!-- ── ACCOUNT HEALTH ────────────────────────────────────────────────────── -->
  <div class="section-title">Account Health</div>
  <div class="cards">
    <div class="card">
      <div class="card-label">Portfolio</div>
      <div class="card-value">${portfolio:,.0f}</div>
    </div>
    <div class="card">
      <div class="card-label">Day P&amp;L</div>
      <div class="card-value {pl_cls}">{pl_sign} ${abs(day_pl):,.2f}</div>
      <div class="card-sub">{pl_pct:+.2f}%</div>
    </div>
    <div class="card">
      <div class="card-label">Cash</div>
      <div class="card-value">${cash:,.0f}</div>
    </div>
    <div class="card">
      <div class="card-label">Buying Power</div>
      <div class="card-value">${bp:,.0f}</div>
    </div>
    <div class="card">
      <div class="card-label">Open Positions</div>
      <div class="card-value">{open_pos}</div>
      <div class="card-sub">of {max_positions} max</div>
    </div>
  </div>

  <div class="gauges">
    <div class="gauge">
      <div class="gauge-label">{cap_icon} Position Capacity — {open_pos} / {max_positions}</div>
      <div class="gauge-track">
        <div class="gauge-fill" style="width:{min(cap_frac*100,100):.0f}%;background:{'#E86060' if cap_frac>=1 else '#F5C400' if cap_frac>=0.67 else '#2D8C7A'}"></div>
      </div>
      <div class="gauge-sub">{cap_frac*100:.0f}% capacity used</div>
    </div>
    <div class="gauge">
      <div class="gauge-label">{loss_icon} Daily Loss Limit — ${loss_used:,.2f} of ${max_loss:,.2f}</div>
      <div class="gauge-track">
        <div class="gauge-fill" style="width:{loss_frac*100:.0f}%;background:{'#E86060' if loss_frac>=0.8 else '#F5C400' if loss_frac>=0.5 else '#2D8C7A'}"></div>
      </div>
      <div class="gauge-sub">{loss_frac*100:.0f}% of 2% daily limit used</div>
    </div>
  </div>

  <!-- ── CRYPTO ─────────────────────────────────────────────────────────────── -->
  <div class="section-title">Crypto — {cx_label}</div>
  <div class="cx-summary">
    <div class="card">
      <div class="card-label">Account Value</div>
      <div class="card-value">${cx_usdt:,.2f}</div>
      <div class="card-sub">USDT total</div>
    </div>
    <div class="card">
      <div class="card-label">Free USDT</div>
      <div class="card-value">${cx_free:,.2f}</div>
      <div class="card-sub">available to trade</div>
    </div>
    <div class="card">
      <div class="card-label">Position Value</div>
      <div class="card-value">${cx_pos_val:,.2f}</div>
      <div class="card-sub">in holdings</div>
    </div>
    <div class="card">
      <div class="card-label">Holdings</div>
      <div class="card-value">{len(cx_positions)}</div>
      <div class="card-sub">{'as of ' + cx_fetched[:16].replace('T',' ') + ' UTC' if cx_fetched else 'no data'}</div>
    </div>
  </div>
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Currency</th>
          <th>Amount</th>
          <th>Price (USD)</th>
          <th>Value (USD)</th>
        </tr>
      </thead>
      <tbody>
        {cx_rows_html}
      </tbody>
    </table>
  </div>

  <div class="footer">
    <span>⚡ FlowTrader · {mode_label} mode · {APP_BUILD}</span>
    <span>Built {built_str}</span>
  </div>

</div><!-- /page -->

<script>
const FT_THEMES = ['theme-light','theme-dark','theme-navy'];
function ftTheme(t, btn) {{
  document.documentElement.classList.remove(...FT_THEMES);
  document.documentElement.classList.add(t);
  localStorage.setItem('ft-theme', t);
  document.querySelectorAll('.theme-bar button').forEach(b => b.classList.toggle('active', b===btn));
}}

// ── Equity chart ──────────────────────────────────────────────────────────────
const chartDates  = {chart_dates};
const chartEquity = {chart_equity};
const chartPL     = {chart_pl};

if (chartDates.length > 0) {{
  const ctx = document.getElementById('equityChart').getContext('2d');
  const validEquity = chartEquity.map((v,i) => (v !== null ? v : undefined));

  // Determine line colour based on net change
  const first = validEquity.find(v => v !== undefined);
  const last  = [...validEquity].reverse().find(v => v !== undefined);
  const lineColour = (last >= first) ? '#2D8C7A' : '#E86060';

  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: chartDates,
      datasets: [{{
        label: 'Portfolio Equity',
        data: validEquity,
        borderColor: lineColour,
        backgroundColor: lineColour + '1A',
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        pointRadius: 3,
        pointHoverRadius: 5,
        spanGaps: true,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: ctx => '$' + ctx.parsed.y.toLocaleString('en-US', {{minimumFractionDigits:2}})
          }}
        }}
      }},
      scales: {{
        x: {{
          grid: {{ color: 'rgba(255,255,255,0.05)' }},
          ticks: {{ color: '#5C5B58', font: {{ family: 'Barlow', size: 11 }}, maxTicksLimit: 10 }}
        }},
        y: {{
          grid: {{ color: 'rgba(255,255,255,0.05)' }},
          ticks: {{
            color: '#5C5B58', font: {{ family: 'Barlow', size: 11 }},
            callback: v => '$' + v.toLocaleString()
          }}
        }}
      }}
    }}
  }});
}} else {{
  document.querySelector('.chart-wrap').innerHTML =
    '<p style="color:var(--color-text-tertiary);text-align:center;padding:60px 0">No portfolio history available</p>';
}}
</script>
</body>
</html>"""


# ── Build constant ─────────────────────────────────────────────────────────────

APP_BUILD = "2026-05-15 · positions-dashboard-v1"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    print("[positions-dash] fetching Alpaca account state…")
    fetcher = None
    account = {}
    try:
        fetcher = MarketDataFetcher()
        account = fetcher.get_account_snapshot()
    except Exception as e:
        print(f"[positions-dash] alpaca-py error: {e}")

    if "error" in account or not account:
        print("[positions-dash] falling back to direct REST…")
        account = _alpaca_direct()

    if "error" in account:
        print(f"[positions-dash] all fetch methods failed: {account['error']} — building with empty data")
    else:
        print(f"[positions-dash] got {account.get('open_positions', 0)} positions, "
              f"portfolio=${account.get('portfolio_value', 0):,.2f}")

    journal       = _load_journal()
    raw_pos       = account.get("positions", [])
    equity_pos    = _enrich_positions(raw_pos, journal)
    crypto_pos    = _infer_crypto_positions(journal)
    enriched      = equity_pos + crypto_pos   # single unified list
    crypto        = _load_crypto_balance()

    print(f"[positions-dash] {len(equity_pos)} equity + {len(crypto_pos)} crypto bot positions")

    history = []
    if fetcher and getattr(fetcher, "trading_client", None):
        history = _fetch_portfolio_history(fetcher)
    if not history:
        history = _alpaca_history_direct()

    built_at = datetime.now(timezone.utc)
    html     = build_html(account, enriched, crypto, history, built_at)

    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"[positions-dash] wrote {OUT_HTML.name} ({len(html):,} bytes)")

    # ── Commit and push ───────────────────────────────────────────────────────
    _git("add", "positions.html")
    if _git("diff", "--cached", "--quiet").returncode == 0:
        print("[positions-dash] no change — skipping commit")
        return 0

    msg = f"chore: positions dashboard {built_at.isoformat()}"
    cm  = _git("commit", "-m", msg)
    if cm.returncode != 0:
        print(f"[positions-dash] commit failed: {cm.stderr.strip()}")
        return 1

    _ensure_gh_user()
    # Pull remote changes first so a fast-forward push always succeeds.
    # (e.g. push_journal.py may have pushed in the same bat session)
    _git("pull", "--no-rebase")
    push = _git("push")
    if push.returncode != 0:
        print(f"[positions-dash] push failed: {push.stderr.strip()}")
        return 1

    print("[positions-dash] pushed positions.html to GitHub Pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
