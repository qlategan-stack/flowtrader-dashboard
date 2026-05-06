"""
dashboard.py — FlowTrader Dashboard
Run locally:  streamlit run dashboard.py
Hosted:       deploy to share.streamlit.io (connects to this GitHub repo)

Auto-refreshes every 60 seconds. Market data cached for 60 s,
journal cached for 30 s. No manual intervention needed.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml
from dotenv import load_dotenv

# ── Secrets: .env locally, st.secrets on Streamlit Cloud ─────────────────────
load_dotenv()
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from data.fetcher import MarketDataFetcher
from data.crypto_fetcher import BybitFetcher
from journal.logger import TradeJournal

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="FlowTrader",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    .stTabs [data-baseweb="tab"] { font-size: 15px; font-weight: 600; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; }
    .badge-paper  { background:#1b3a4b; color:#4cc9f0; padding:3px 10px;
                    border-radius:12px; font-size:0.8rem; font-weight:600; }
    .badge-live   { background:#3b1f2b; color:#f72585; padding:3px 10px;
                    border-radius:12px; font-size:0.8rem; font-weight:600; }
    .badge-a      { background:#1b4332; color:#40916c; padding:2px 8px;
                    border-radius:8px; font-weight:700; }
    .badge-b      { background:#1b3a4b; color:#4cc9f0; padding:2px 8px;
                    border-radius:8px; font-weight:700; }
    .badge-c      { background:#2d2a1e; color:#ffd60a; padding:2px 8px;
                    border-radius:8px; font-weight:700; }
    .badge-skip   { color:#555; }
</style>
""", unsafe_allow_html=True)

# ── Config ────────────────────────────────────────────────────────────────────
with open("config.yaml") as f:
    CFG = yaml.safe_load(f)
WATCHLIST     = CFG.get("watchlist", {}).get("equities", ["SPY", "QQQ"])
CRYPTO_LIST   = CFG.get("watchlist", {}).get("crypto", [])
RISK_PROFILES = CFG.get("risk_profiles", {})
REFRESH_SEC   = 60
PAPER_MODE    = os.getenv("PAPER_TRADING", "true").lower() == "true"

MEMO_JSON         = Path("journal/weekly_research_memo.json")
RISK_PROFILE_JSON = Path("journal/risk_profile.json")
_GH_REPO          = "qlategan-stack/flowtrader-dashboard"
_GH_PROFILE_PATH  = "journal/risk_profile.json"


def _read_active_profile() -> str:
    try:
        return json.loads(RISK_PROFILE_JSON.read_text(encoding="utf-8")).get("active_profile", "high_safety")
    except Exception:
        return "high_safety"


def _write_profile_to_github(profile_name: str) -> bool:
    """Commit risk_profile.json to GitHub via API so the bot picks it up on next pull."""
    import base64
    import requests as _req
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        return False
    content = json.dumps({"active_profile": profile_name, "updated_at": datetime.utcnow().isoformat() + "Z"}, indent=2) + "\n"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    url = f"https://api.github.com/repos/{_GH_REPO}/contents/{_GH_PROFILE_PATH}"
    sha = ""
    try:
        r = _req.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            sha = r.json().get("sha", "")
    except Exception:
        pass
    payload = {
        "message": f"chore: set risk profile to {profile_name}",
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha
    try:
        r = _req.put(url, headers=headers, json=payload, timeout=10)
        return r.status_code in (200, 201)
    except Exception:
        return False

SYMBOL_NAMES = {
    "NVDA": "NVIDIA Corporation",
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "AMD": "Advanced Micro Devices",
    "TSLA": "Tesla Inc.",
    "META": "Meta Platforms Inc.",
    "SPY": "S&P 500 ETF (SPDR)",
    "QQQ": "Nasdaq-100 ETF (Invesco)",
    "IWM": "Russell 2000 Small-Cap ETF",
    "GLD": "Gold ETF (SPDR)",
    "SLV": "Silver ETF (iShares)",
    "USO": "US Oil Fund ETF (WTI Crude)",
    "TLT": "20+ Year Treasury Bond ETF",
    "GOOGL": "Alphabet Inc. (Google)",
    "AMZN": "Amazon.com Inc.",
    "JPM": "JPMorgan Chase & Co.",
    "COIN": "Coinbase Global Inc.",
    "MSTR": "MicroStrategy Inc.",
    "RIOT": "Riot Platforms Inc.",
    "XOM": "ExxonMobil Corporation",
    "CVX": "Chevron Corporation",
    "BTC/USDT":  "Bitcoin / Tether USD",
    "ETH/USDT":  "Ethereum / Tether USD",
    "XRP/USDT":  "Ripple / Tether USD",
    "LTC/USDT":  "Litecoin / Tether USD",
    "SOL/USDT":  "Solana / Tether USD",
    "ADA/USDT":  "Cardano / Tether USD",
    "DOT/USDT":  "Polkadot / Tether USD",
    "LINK/USDT": "Chainlink / Tether USD",
    "NEAR/USDT": "NEAR Protocol / Tether USD",
}

# ── Cached data ───────────────────────────────────────────────────────────────
@st.cache_resource
def _fetcher():
    # Re-apply secrets/env at creation time so cache misses don't lose keys
    try:
        for _k, _v in st.secrets.items():
            os.environ[_k] = str(_v)
    except Exception:
        pass
    return MarketDataFetcher()

@st.cache_resource
def _bybit():
    try:
        for _k, _v in st.secrets.items():
            os.environ[_k] = str(_v)
    except Exception:
        pass
    return BybitFetcher()

@st.cache_resource
def _journal():
    return TradeJournal()

@st.cache_data(ttl=REFRESH_SEC)
def fetch_account():
    return _fetcher().get_account_snapshot()

@st.cache_data(ttl=REFRESH_SEC)
def fetch_snapshot(watchlist: tuple):
    return _fetcher().build_market_snapshot(list(watchlist))

@st.cache_data(ttl=REFRESH_SEC)
def fetch_crypto_snapshot(symbols: tuple):
    return _bybit().build_crypto_snapshot(list(symbols))

BYBIT_BALANCE_JSON = Path("journal/bybit_balance.json")

@st.cache_data(ttl=REFRESH_SEC)
def fetch_bybit_balance():
    if BYBIT_BALANCE_JSON.exists():
        try:
            data = json.loads(BYBIT_BALANCE_JSON.read_text(encoding="utf-8"))
            if "error" not in data:
                return data
        except Exception:
            pass
    return _bybit().get_balance()

@st.cache_data(ttl=30)
def fetch_entries(days: int):
    return _journal().get_entries(days=days)

@st.cache_data(ttl=60)
def load_research_memo() -> dict:
    """Load the weekly research memo.  TTL kept short so a fresh memo
    appears within ~1 minute of the sync workflow committing it."""
    if not MEMO_JSON.exists():
        return {}
    try:
        return json.loads(MEMO_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}

# ── Sidebar — Control Panel ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Control Panel")
    st.caption(f"{'🟡 PAPER' if PAPER_MODE else '🔴 LIVE'} mode active")
    st.divider()

    # ── Data refresh ──────────────────────────────────────────────────────────
    st.markdown("**Data**")
    if st.button("⟳ Refresh All Data", width="stretch", help="Clear cache and reload market data, account, and journal"):
        st.cache_data.clear()
        st.session_state.next_refresh = time.time() + REFRESH_SEC
        st.rerun()

    if st.button("⟳ Refresh Market Only", width="stretch", help="Re-fetch watchlist prices and indicators"):
        fetch_snapshot.clear()
        st.rerun()

    if st.button("⟳ Refresh Account Only", width="stretch", help="Re-fetch account balance and positions"):
        fetch_account.clear()
        st.rerun()

    st.divider()

    # ── Risk Profile ──────────────────────────────────────────────────────────
    st.markdown("**Risk Profile**")
    current_profile = _read_active_profile()
    profile_options = ["high_safety", "medium_safety", "low_safety"]
    profile_labels  = {"high_safety": "🟢 High Safety", "medium_safety": "🟡 Medium Safety", "low_safety": "🔴 Low Safety"}

    selected_profile = st.radio(
        "Active profile",
        profile_options,
        format_func=lambda x: profile_labels[x],
        index=profile_options.index(current_profile) if current_profile in profile_options else 0,
        label_visibility="collapsed",
    )

    if selected_profile != current_profile:
        if _write_profile_to_github(selected_profile):
            st.success(f"Switched to **{profile_labels[selected_profile]}**. Takes effect on next bot run (~30 min).")
        else:
            st.error("Could not save — add GITHUB_TOKEN to Streamlit secrets.")

    with st.expander("Profile limits"):
        p = RISK_PROFILES.get(selected_profile, {})
        rows = {
            "Max positions":    str(p.get("max_open_positions", "—")),
            "Daily loss cap":   f"{p.get('max_daily_loss_pct', 0)*100:.0f}%",
            "Max position size":f"{p.get('max_position_pct', 0)*100:.0f}% of account",
            "Risk per trade":   f"{p.get('risk_pct_per_trade', 0)*100:.1f}%",
            "Min signal score": f"{p.get('min_signal_score', '—')}/6",
            "Max stop distance":f"{p.get('max_stop_distance_pct', 0)*100:.0f}%",
            "Min order value":  f"${p.get('min_order_value', '—')}",
        }
        for label, val in rows.items():
            c1, c2 = st.columns([3, 2])
            c1.caption(label)
            c2.caption(f"**{val}**")

    st.divider()

    # ── Bot info ──────────────────────────────────────────────────────────────
    st.markdown("**Trading Bot**")
    st.caption("The bot runs locally via Windows Task Scheduler every 30 min. Journal data syncs here automatically.")

    st.divider()
    st.markdown("**Auto-refresh**")
    auto_refresh = st.toggle("Auto-refresh every 60 s", value=True,
                             help="Toggle to pause the automatic page refresh")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _grade_colour(grade: str) -> str:
    return {
        "A_GRADE": "color:#40916c;font-weight:700",
        "B_GRADE": "color:#4cc9f0;font-weight:700",
        "C_GRADE": "color:#ffd60a;font-weight:700",
    }.get(grade, "color:#555")

def _score_colour(v):
    if v >= 5:   return "color:#00c853;font-weight:bold"
    elif v >= 3: return "color:#ffab00;font-weight:bold"
    return "color:#ff5252"

def _regime_colour(v):
    return "color:#ff5252;font-weight:bold" if v == "TRENDING" else "color:#00c853"

def _action_colour(v):
    return {
        "BUY":  "background-color:#1b4332;color:#40916c;font-weight:bold",
        "SELL": "background-color:#3b1f2b;color:#f72585;font-weight:bold",
        "SKIP": "color:#555",
        "HOLD": "color:#ffab00",
    }.get(v, "")

def _exec_colour(v):
    return {
        "FILLED":    "color:#00c853",
        "SUBMITTED": "color:#4cc9f0",
        "REJECTED":  "color:#ff5252",
        "SKIPPED":   "color:#555",
        "SIMULATED": "color:#ffab00",
    }.get(v, "")

def _sentiment_colour(v):
    return {"positive": "color:#00c853", "negative": "color:#ff5252"}.get(v, "color:#888")

def _dark_bar(vals, dates, yprefix="$", height=220):
    fig = go.Figure(go.Bar(
        x=dates, y=vals,
        marker_color=["#00c853" if v >= 0 else "#ff5252" for v in vals],
        marker_line_width=0,
    ))
    fig.update_layout(
        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
        font_color="#fafafa", height=height,
        margin=dict(l=0, r=0, t=4, b=0),
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="#1c1f26", tickprefix=yprefix),
    )
    return fig

# ── Header ────────────────────────────────────────────────────────────────────
now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
mode_badge = '<span class="badge-paper">PAPER</span>' if PAPER_MODE else '<span class="badge-live">LIVE</span>'

# App build marker — bumped whenever the rendered features change.  Lets the
# user verify which version Streamlit Cloud is actually serving.
APP_BUILD = "2026-05-07 · crypto-outlook"

h1, h2, h3 = st.columns([5, 2, 1])
h1.markdown(f"## 📈 FlowTrader  {mode_badge}", unsafe_allow_html=True)
h2.caption(f"Updated: {now_str}  ·  Build: {APP_BUILD}")
if h3.button("⟳ Refresh", width="stretch"):
    st.cache_data.clear()
    st.rerun()

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_market, tab_account, tab_journal, tab_research, tab_learn = st.tabs([
    "🔍 Market", "💼 Account", "📓 Journal", "🧠 Research", "📚 Learn"
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — MARKET
# ═══════════════════════════════════════════════════════════════════════════════
with tab_market:

    with st.spinner("Fetching market data…"):
        snapshot = fetch_snapshot(tuple(WATCHLIST))

    wl = snapshot.get("watchlist", [])

    if not wl:
        st.warning("No market data — check your Alpaca API keys.")
        st.stop()

    # ── Summary metrics ───────────────────────────────────────────────────────
    _active_prof_name = _read_active_profile()
    _active_prof      = RISK_PROFILES.get(_active_prof_name, {})
    _min_score        = _active_prof.get("min_signal_score", 3)

    tradeable = [s for s in wl if s.get("setup_quality") not in ["SKIP", "NO_DATA"]]
    top       = wl[0] if wl else {}
    trending  = sum(1 for s in wl if s.get("indicators", {}).get("regime") == "TRENDING")
    a_grades  = sum(1 for s in wl if s.get("setup_quality") == "A_GRADE")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Symbols Scanned", len(wl),
              help="Total symbols in your active watchlist being scanned this cycle.")
    c2.metric("Tradeable Setups", len(tradeable),
              help=f"Symbols with {_min_score}+ signals fired AND in a ranging market (ADX < 25). These are candidates for a trade. Threshold set by active risk profile ({_active_prof_name}).")
    c3.metric("A-Grade Setups", a_grades,
              help="Highest quality setups: 5+ signals fired. Rare — only enter on these if available.")
    c4.metric("Top Signal Score",
              f"{top.get('indicators',{}).get('signal_score',0)}/6",
              top.get("symbol", "—"),
              help="Best signal score across all symbols right now. Need ≥ 3/6 to consider a trade.")
    c5.metric("Trending (skip)", trending, delta_color="inverse",
              help="Symbols where ADX > 25 — the market is trending strongly. Mean reversion does NOT work in trending markets, so these are skipped.")

    st.divider()

    # ── Ticker reference key ──────────────────────────────────────────────────
    with st.expander("📖 Ticker Reference — what do these symbols mean?"):
        key_cols = st.columns(3)
        all_syms = list(WATCHLIST) + list(CRYPTO_LIST)
        for i, sym in enumerate(all_syms):
            name = SYMBOL_NAMES.get(sym, sym)
            key_cols[i % 3].markdown(f"**`{sym}`** — {name}")

    st.caption("💡 **Price** updates live every 60 s. **RSI / BB / ADX** are calculated from daily bars and only change once per day at market close — this is expected.")

    # ── Watchlist table ───────────────────────────────────────────────────────
    st.subheader("Equities Watchlist")
    rows = []
    for item in wl:
        ind  = item.get("indicators", {})
        sent = item.get("news_sentiment", {})
        rows.append({
            "Symbol":    item["symbol"],
            "Grade":     item.get("setup_quality", "—"),
            "Score":     ind.get("signal_score", 0),
            "Price":     ind.get("current_price", 0),
            "RSI":       ind.get("rsi", 0),
            "ADX":       ind.get("adx", 0),
            "Regime":    ind.get("regime", "—"),
            "BB %B":     ind.get("bollinger", {}).get("pct_b", 0),
            "MA20":      ind.get("ma20", 0),
            "ATR":       ind.get("atr", 0),
            "Stop":      ind.get("stop_loss_price", 0),
            "Target":    ind.get("take_profit_price", 0),
            "Sentiment": sent.get("sentiment", "neutral"),
            "Signals":   ", ".join(ind.get("signals_fired", [])) or "none",
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df.style
          .map(_grade_colour,   subset=["Grade"])
          .map(_score_colour,   subset=["Score"])
          .map(_regime_colour,  subset=["Regime"])
          .map(_sentiment_colour, subset=["Sentiment"])
          .format({
              "Price":  "${:,.2f}", "MA20": "${:,.2f}",
              "Stop":   "${:,.2f}", "Target": "${:,.2f}",
              "ATR":    "{:.2f}",   "BB %B": "{:.3f}",
              "RSI":    "{:.1f}",   "ADX":   "{:.1f}",
          }),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Symbol":    st.column_config.TextColumn("Symbol",    help="Ticker symbol. Hover the 📖 Ticker Reference above for full names."),
            "Grade":     st.column_config.TextColumn("Grade",     help=f"Setup quality (profile: {_active_prof_name}): A_GRADE (≥{_min_score+2} signals) → best | B_GRADE (≥{_min_score+1}) | C_GRADE (≥{_min_score}) | SKIP | NO_DATA"),
            "Score":     st.column_config.NumberColumn("Score",   help=f"Signal score 0–6. Need ≥ {_min_score} to trade ({_active_prof_name} profile). Each indicator that fires adds 1 point (RSI<32 adds 2)."),
            "Price":     st.column_config.NumberColumn("Price",   help="Current price of the asset in USD."),
            "RSI":       st.column_config.NumberColumn("RSI",     help="Relative Strength Index (0–100). Below 32 = oversold (potential BUY). Above 68 = overbought. Best for mean reversion when very low."),
            "ADX":       st.column_config.NumberColumn("ADX",     help="Average Directional Index. Below 20 = ranging market (good for mean reversion). Above 25 = trending (SKIP — strategy doesn't work in trends)."),
            "Regime":    st.column_config.TextColumn("Regime",    help="RANGING = ADX < 25, mean reversion works here. TRENDING = ADX > 25, skip this symbol."),
            "BB %B":     st.column_config.NumberColumn("BB %B",   help="Bollinger Band position. 0 = at lower band (oversold zone). 1 = at upper band (overbought). Negative = below lower band — strong signal."),
            "MA20":      st.column_config.NumberColumn("MA20",    help="20-day Moving Average — the 'mean' price the bot targets as take-profit. Price reverts toward this."),
            "ATR":       st.column_config.NumberColumn("ATR",     help="Average True Range — measures daily volatility in $. Used to set stop-loss distance (stop = entry − 0.5×ATR)."),
            "Stop":      st.column_config.NumberColumn("Stop",    help="Calculated stop-loss price = current price − (0.5 × ATR). Bot exits automatically if price falls here."),
            "Target":    st.column_config.NumberColumn("Target",  help="Take-profit target = MA20. The strategy exits when price reverts back to its 20-day average."),
            "Sentiment": st.column_config.TextColumn("Sentiment", help="News sentiment scored from recent headlines. Positive adds +1 to signal score. Negative is a caution flag."),
            "Signals":   st.column_config.TextColumn("Signals",   help="Which specific signals fired for this symbol. Need 3+ to qualify for a trade."),
        },
    )

    # ── Signal score bar chart ────────────────────────────────────────────────
    if rows:
        score_fig = go.Figure(go.Bar(
            x=[r["Symbol"] for r in rows],
            y=[r["Score"] for r in rows],
            marker_color=["#00c853" if r["Score"] >= 5 else "#ffab00" if r["Score"] >= 3 else "#ff5252" for r in rows],
            marker_line_width=0,
            text=[r["Grade"].replace("_GRADE","") for r in rows],
            textposition="outside",
        ))
        score_fig.update_layout(
            plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", font_color="#fafafa",
            height=200, margin=dict(l=0, r=0, t=8, b=0),
            yaxis=dict(range=[0, 7], gridcolor="#1c1f26", title="Signal Score"),
            xaxis=dict(showgrid=False),
            showlegend=False,
        )
        st.plotly_chart(score_fig, use_container_width=True)

    # (Symbol Detail moved to a single combined expander below the crypto section)

    # ── Crypto watchlist — live market data ──────────────────────────────────
    crypto_wl = []  # ensure defined for the unified Symbol Detail block below
    if CRYPTO_LIST:
        st.divider()
        _b           = _bybit()
        order_mode   = "🟡 TESTNET" if _b.testnet else "🔴 LIVE"
        has_key      = _b._has_private
        st.subheader(f"Crypto Watchlist — Orders: Bybit {order_mode}")
        st.caption("💡 **Price** updates live every 60 s via Binance ticker. **RSI / BB / ADX** are calculated from daily bars and only change once per day at midnight UTC.")

        with st.spinner("Fetching crypto market data…"):
            crypto_wl = fetch_crypto_snapshot(tuple(CRYPTO_LIST))

        active_source = getattr(_b, "_active_source", None)
        source_label = {
            "coinbase": "Coinbase Exchange",
            "kraken":   "Kraken",
            "binance":  "Binance",
            "bybit":    "Bybit",
            "ccxt":     "ccxt (Bybit)",
        }.get(active_source, "no source reachable")
        st.caption(
            f"📡 Market data source: **{source_label}**  ·  "
            f"Order routing: Bybit {'Testnet' if _b.testnet else 'Live'} "
            f"({'API key loaded' if has_key else 'no key — read-only'})"
        )

        if not has_key:
            st.caption("Add BYBIT_API_KEY + BYBIT_SECRET_KEY to Streamlit secrets to enable order placement.")

        if crypto_wl:
            crows = []
            for item in crypto_wl:
                ind    = item.get("indicators", {})
                ticker = item.get("ticker", {})
                crows.append({
                    "Symbol":    item["symbol"],
                    "Grade":     item.get("setup_quality", "—"),
                    "Score":     ind.get("signal_score", 0),
                    "Price":     ind.get("current_price", ticker.get("price", 0)),
                    "RSI":       ind.get("rsi", 0),
                    "ADX":       ind.get("adx", 0),
                    "Regime":    ind.get("regime", "—"),
                    "BB %B":     ind.get("bollinger", {}).get("pct_b", 0),
                    "MA20":      ind.get("ma20", 0),
                    "ATR":       ind.get("atr", 0),
                    "Stop":      ind.get("stop_loss_price", 0),
                    "Target":    ind.get("take_profit_price", 0),
                    "24h %":     ticker.get("change_pct_24h", 0),
                    "Signals":   ", ".join(ind.get("signals_fired", [])) or "none",
                })

            cdf = pd.DataFrame(crows)

            def _change_colour(v):
                return "color:#00c853;font-weight:bold" if v >= 0 else "color:#ff5252;font-weight:bold"

            st.dataframe(
                cdf.style
                   .map(_grade_colour,   subset=["Grade"])
                   .map(_score_colour,   subset=["Score"])
                   .map(_regime_colour,  subset=["Regime"])
                   .map(_change_colour,  subset=["24h %"])
                   .format({
                       "Price":  "${:,.4f}", "MA20": "${:,.4f}",
                       "Stop":   "${:,.4f}", "Target": "${:,.4f}",
                       "ATR":    "{:.4f}",   "BB %B": "{:.3f}",
                       "RSI":    "{:.1f}",   "ADX":   "{:.1f}",
                       "24h %":  "{:+.2f}%",
                   }),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Symbol":  st.column_config.TextColumn("Symbol",  help="Crypto trading pair on Bybit spot market."),
                    "Grade":   st.column_config.TextColumn("Grade",   help="Setup quality based on signal score."),
                    "Score":   st.column_config.NumberColumn("Score", help="Signal score 0–6. Need ≥ 3 to trade."),
                    "24h %":   st.column_config.NumberColumn("24h %", help="Price change over the last 24 hours on Bybit."),
                    "RSI":     st.column_config.NumberColumn("RSI",   help="Relative Strength Index. < 32 = oversold."),
                    "ADX":     st.column_config.NumberColumn("ADX",   help="Trend strength. > 25 = trending (skip)."),
                    "Regime":  st.column_config.TextColumn("Regime",  help="RANGING = ADX < 25 (mean reversion active)."),
                    "BB %B":   st.column_config.NumberColumn("BB %B", help="Position within Bollinger Bands. 0 = lower band."),
                    "ATR":     st.column_config.NumberColumn("ATR",   help="Average True Range — daily volatility in USD."),
                    "Stop":    st.column_config.NumberColumn("Stop",  help="Stop-loss = price − 0.5×ATR."),
                    "Target":  st.column_config.NumberColumn("Target",help="Take-profit target = MA20."),
                },
            )

    # ── Unified Symbol Detail (equities + crypto in one collapsed expander) ──
    st.divider()
    with st.expander("📊 Symbol Detail — click to inspect a ticker or pair", expanded=False):
        eq_tab, cr_tab = st.tabs(["📈 Equities", "₿ Crypto"])

        with eq_tab:
            if wl:
                eq_lookup  = {item["symbol"]: item for item in wl}
                eq_symbols = list(eq_lookup.keys())

                def _eq_pill_label(sym: str) -> str:
                    it = eq_lookup[sym]
                    grade = it.get("setup_quality", "SKIP")
                    score = it.get("indicators", {}).get("signal_score", 0)
                    dot = "🟢" if grade in ("A_GRADE", "B_GRADE") else "🟡" if grade == "C_GRADE" else "🔴"
                    return f"{dot} {sym} · {score}"

                selected_sym = st.pills(
                    "Select a symbol",
                    options=eq_symbols,
                    default=None,
                    format_func=_eq_pill_label,
                    label_visibility="collapsed",
                    key="equity_detail_pill",
                )

                if selected_sym:
                    item  = eq_lookup[selected_sym]
                    ind   = item.get("indicators", {})
                    sent  = item.get("news_sentiment", {})
                    grade = item.get("setup_quality", "SKIP")
                    score = ind.get("signal_score", 0)
                    full  = SYMBOL_NAMES.get(selected_sym, "")

                    st.markdown(
                        f"**{selected_sym}**" + (f" — {full}" if full else "") +
                        f"   ·   Score {score}/6   ·   {grade}   ·   {ind.get('regime','?')}"
                    )

                    d1, d2, d3, d4 = st.columns(4)
                    d1.metric("Price", f"${ind.get('current_price',0):,.2f}")
                    d1.metric("MA20",  f"${ind.get('ma20',0):,.2f}")
                    d1.metric("MA50",  f"${ind.get('ma50',0):,.2f}")
                    d2.metric("RSI",   f"{ind.get('rsi',0):.1f}")
                    d2.metric("ADX",   f"{ind.get('adx',0):.1f}")
                    d2.metric("ATR",   f"{ind.get('atr',0):.2f}")
                    bb = ind.get("bollinger", {})
                    d3.metric("BB Upper", f"${bb.get('upper',0):,.2f}")
                    d3.metric("BB Mid",   f"${bb.get('middle',0):,.2f}")
                    d3.metric("BB Lower", f"${bb.get('lower',0):,.2f}")
                    d4.metric("VWAP",   f"${ind.get('vwap',0):,.2f}")
                    d4.metric("Stop",   f"${ind.get('stop_loss_price',0):,.2f}")
                    d4.metric("Target", f"${ind.get('take_profit_price',0):,.2f}")

                    fired = ind.get("signals_fired", [])
                    if fired:
                        st.success("Signals fired:  " + "  ·  ".join(fired))
                    else:
                        st.info("No signals fired")

                    sent_score = sent.get("score", 0)
                    sent_label = sent.get("sentiment", "neutral")
                    sent_icon  = "📈" if sent_label == "positive" else "📉" if sent_label == "negative" else "➡️"
                    st.caption(f"{sent_icon} Sentiment: **{sent_label}** (score {sent_score:+.3f}, {sent.get('article_count', 0)} articles)")

                    for h in item.get("recent_headlines", [])[:3]:
                        pub = h.get("published", "")[:10]
                        st.caption(f"[{pub}] **{h.get('source','')}** — {h.get('headline','')}")

        with cr_tab:
            if CRYPTO_LIST and crypto_wl:
                cr_lookup  = {item["symbol"]: item for item in crypto_wl}
                cr_symbols = list(cr_lookup.keys())

                def _cr_pill_label(sym: str) -> str:
                    it = cr_lookup[sym]
                    grade = it.get("setup_quality", "SKIP")
                    score = it.get("indicators", {}).get("signal_score", 0)
                    dot = "🟢" if grade in ("A_GRADE", "B_GRADE") else "🟡" if grade == "C_GRADE" else "🔴"
                    return f"{dot} {sym} · {score}"

                selected_cr = st.pills(
                    "Select a crypto pair",
                    options=cr_symbols,
                    default=None,
                    format_func=_cr_pill_label,
                    label_visibility="collapsed",
                    key="crypto_detail_pill",
                )

                if selected_cr:
                    item   = cr_lookup[selected_cr]
                    ind    = item.get("indicators", {})
                    ticker = item.get("ticker", {})
                    grade  = item.get("setup_quality", "SKIP")
                    score  = ind.get("signal_score", 0)
                    full   = SYMBOL_NAMES.get(selected_cr, selected_cr)

                    st.markdown(
                        f"**{selected_cr}** — {full}   ·   Score {score}/6   ·   {grade}   ·   {ind.get('regime','?')}"
                    )

                    d1, d2, d3, d4 = st.columns(4)
                    d1.metric("Price", f"${ind.get('current_price', ticker.get('price',0)):,.4f}")
                    d1.metric("MA20",  f"${ind.get('ma20',0):,.4f}")
                    d1.metric("24h %", f"{ticker.get('change_pct_24h',0):+.2f}%")
                    d2.metric("RSI",   f"{ind.get('rsi',0):.1f}")
                    d2.metric("ADX",   f"{ind.get('adx',0):.1f}")
                    d2.metric("ATR",   f"${ind.get('atr',0):,.4f}")
                    bb = ind.get("bollinger", {})
                    d3.metric("BB Upper", f"${bb.get('upper',0):,.4f}")
                    d3.metric("BB Mid",   f"${bb.get('middle',0):,.4f}")
                    d3.metric("BB Lower", f"${bb.get('lower',0):,.4f}")
                    d4.metric("VWAP",   f"${ind.get('vwap',0):,.4f}")
                    d4.metric("Stop",   f"${ind.get('stop_loss_price',0):,.4f}")
                    d4.metric("Target", f"${ind.get('take_profit_price',0):,.4f}")

                    fired = ind.get("signals_fired", [])
                    if fired:
                        st.success("Signals fired:  " + "  ·  ".join(fired))
                    else:
                        st.info("No signals fired")

                    vol = ticker.get("volume_24h_usdt", 0)
                    if vol:
                        st.caption(
                            f"24h volume: ${vol:,.0f} USDT  ·  "
                            f"High: ${ticker.get('high_24h',0):,.4f}  ·  "
                            f"Low: ${ticker.get('low_24h',0):,.4f}"
                        )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ACCOUNT
# ═══════════════════════════════════════════════════════════════════════════════
with tab_account:

    with st.spinner("Fetching account data…"):
        acct = fetch_account()

    if "error" in acct:
        st.error(f"Could not load account: {acct['error']}")
        if acct["error"] == "Alpaca client not initialized":
            st.info(
                "**To fix this**, create `.streamlit/secrets.toml` by copying "
                "`.streamlit/secrets.toml.example` and filling in your Alpaca API keys.  \n"
                "On Streamlit Cloud, paste the same keys into **App settings → Secrets**."
            )
    else:
        _acct_prof_name = _read_active_profile()
        _acct_prof      = RISK_PROFILES.get(_acct_prof_name, {})
        _max_positions  = _acct_prof.get("max_open_positions", 3)
        _max_loss_pct   = _acct_prof.get("max_daily_loss_pct", 0.02)

        # ── Alpaca figures ────────────────────────────────────────────────────────
        portfolio  = float(acct.get("portfolio_value", 0))
        buying_pwr = float(acct.get("buying_power", 0))
        cash       = float(acct.get("cash", 0))
        day_pl     = float(acct.get("day_pl", 0))
        positions  = acct.get("positions", []) or []
        open_pos   = len(positions)
        invested   = portfolio - cash

        # ── Bybit figures (used in combined header) ───────────────────────────────
        bybit_bal      = fetch_bybit_balance()
        bybit_ok       = "error" not in bybit_bal
        bybit_value    = float(bybit_bal.get("account_value", 0))  if bybit_ok else 0.0
        bybit_free     = float(bybit_bal.get("free_usdt", 0))      if bybit_ok else 0.0
        bybit_funding  = float(bybit_bal.get("funding_usdt", 0))   if bybit_ok else 0.0
        bybit_invested = float(bybit_bal.get("position_value", 0)) if bybit_ok else 0.0
        bybit_holds    = bybit_bal.get("positions", []) or []
        bybit_npos     = len(bybit_holds)

        # ═════════════════════════════════════════════════════════════════════════
        # 🌐 TOTAL — combined view across both venues
        # ═════════════════════════════════════════════════════════════════════════
        st.markdown("### 🌐 Total Account  —  Alpaca + Bybit Combined")

        total_value     = portfolio + bybit_value
        total_available = buying_pwr + bybit_free
        total_invested  = invested + bybit_invested
        total_positions = open_pos + bybit_npos

        t1, t2, t3, t4, t5 = st.columns(5)
        t1.metric("Total Value",       f"${total_value:,.2f}",
                  help="Total wealth across both venues — Alpaca equity portfolio + Bybit USDT and crypto holdings.")
        t2.metric("Available to Trade", f"${total_available:,.2f}",
                  help="Cash you can deploy right now: Alpaca buying power + Bybit free USDT (in the Unified wallet).")
        t3.metric("Currently Invested", f"${total_invested:,.2f}",
                  f"{(total_invested/total_value*100):.1f}%" if total_value else "—",
                  help="Capital tied up in open positions across both venues.")
        t4.metric("Day P&L (Alpaca)",  f"${day_pl:+,.2f}",
                  f"{day_pl/portfolio*100:+.2f}%" if portfolio else "0%",
                  delta_color="normal",
                  help="Today's realised + unrealised change on the Alpaca account. Bybit doesn't expose a comparable daily P&L figure.")
        t5.metric("Open Positions",    f"{total_positions}",
                  f"{open_pos} equity · {bybit_npos} crypto",
                  help="Combined count across both venues.")

        st.divider()

        # ═════════════════════════════════════════════════════════════════════════
        # 📈 ALPACA — US Equities
        # ═════════════════════════════════════════════════════════════════════════
        st.markdown(f"### 📈 Alpaca  —  US Equities  ·  ${portfolio:,.2f}")

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Account Value",  f"${portfolio:,.2f}",
                  help="Total Alpaca account equity = cash + market value of held positions.")
        a2.metric("Cash",           f"${cash:,.2f}",
                  help="Settled cash currently sitting in the account (not borrowed).")
        a3.metric("Buying Power",   f"${buying_pwr:,.2f}",
                  help="Maximum amount you can use to open new positions. On a paper account this includes margin headroom; on cash it equals settled cash.")
        a4.metric("Day P&L",        f"${day_pl:+,.2f}",
                  f"{day_pl/portfolio*100:+.2f}%" if portfolio else "0%",
                  delta_color="normal",
                  help="Change in account equity since yesterday's close.")

        # ── Risk gauges ───────────────────────────────────────────────────────────
        gcol1, gcol2 = st.columns(2)

        with gcol1:
            cap_frac = open_pos / _max_positions if _max_positions else 0
            cap_icon = "🔴" if cap_frac >= 1.0 else "🟡" if cap_frac >= 0.67 else "🟢"
            st.markdown("**Position Capacity**")
            st.progress(min(cap_frac, 1.0),
                        text=f"{cap_icon} {open_pos} / {_max_positions} positions used  ·  profile: {_acct_prof_name}")

        with gcol2:
            max_loss  = portfolio * _max_loss_pct
            loss_used = abs(day_pl) if day_pl < 0 else 0
            loss_frac = min(loss_used / max_loss, 1.0) if max_loss else 0
            loss_icon = "🔴" if loss_frac >= 0.8 else "🟡" if loss_frac >= 0.5 else "🟢"
            st.markdown("**Daily Loss Limit**")
            st.progress(loss_frac,
                        text=f"{loss_icon} ${loss_used:,.2f} of ${max_loss:,.2f} max  ·  {loss_frac:.0%} used")

        # ── Open positions table — enriched with stop/target/days/score ───────────
        st.markdown(f"**Open Equity Positions ({open_pos})**")

        if not positions:
            st.info("No open Alpaca positions.")
        else:
            # Build a lookup: most recent BUY journal entry per symbol → has the
            # original stop_loss, take_profit, signal_score, entry timestamp.
            recent_entries = fetch_entries(30)  # 30 days is plenty
            entry_by_sym: dict = {}
            for e in recent_entries:
                if e.get("action") == "BUY" and e.get("execution_status") in ("FILLED", "SUBMITTED", "SIMULATED"):
                    sym = e.get("symbol")
                    if sym and sym not in entry_by_sym:  # most-recent-first iteration not guaranteed; capture latest below
                        entry_by_sym[sym] = e
                    elif sym and e.get("timestamp", "") > entry_by_sym[sym].get("timestamp", ""):
                        entry_by_sym[sym] = e

            from datetime import date as _date_cls, datetime as _dt_cls

            prows = []
            for p in positions:
                sym       = p.get("symbol", "")
                qty       = float(p.get("qty", 0))
                entry_px  = float(p.get("avg_entry", 0))
                cur_px    = float(p.get("current_price", 0))
                pl        = float(p.get("unrealized_pl", 0))
                plpct     = float(p.get("unrealized_plpc", 0)) * 100

                je = entry_by_sym.get(sym, {})
                stop      = float(je.get("stop_loss",   0) or 0)
                target    = float(je.get("take_profit", 0) or 0)
                score     = je.get("signal_score", "—")

                # R:R remaining = distance to target / distance to stop, from CURRENT price
                rr_left = "—"
                if stop > 0 and target > 0 and cur_px > 0:
                    risk   = max(cur_px - stop, 0.01)
                    reward = max(target - cur_px, 0)
                    rr_left = f"1:{reward/risk:.1f}" if reward > 0 else "0"

                # Days held
                days_held = "—"
                ts = je.get("timestamp", "")
                if ts:
                    try:
                        opened = _dt_cls.fromisoformat(ts).date()
                        days_held = (_date_cls.today() - opened).days
                    except Exception:
                        pass

                prows.append({
                    "Symbol":   sym,
                    "Source":   "🤖 Bot" if je else "👤 Manual",
                    "Qty":      qty,
                    "Entry":    entry_px,
                    "Current":  cur_px,
                    "Stop":     stop if stop > 0 else None,
                    "Target":   target if target > 0 else None,
                    "P&L $":    pl,
                    "P&L %":    plpct,
                    "R:R Left": rr_left,
                    "Days":     days_held,
                    "Score":    score,
                })

            pdf = pd.DataFrame(prows)

            def _pl_colour(v):
                if isinstance(v, (int, float)):
                    return "color:#00c853;font-weight:bold" if v >= 0 else "color:#ff5252;font-weight:bold"
                return ""

            st.dataframe(
                pdf.style
                   .map(_pl_colour, subset=["P&L $", "P&L %"])
                   .format({
                       "Entry":    "${:,.2f}",
                       "Current":  "${:,.2f}",
                       "Stop":     "${:,.2f}",
                       "Target":   "${:,.2f}",
                       "P&L $":    "${:+,.2f}",
                       "P&L %":    "{:+.2f}%",
                       "Qty":      "{:,.4f}",
                   }, na_rep="—"),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "📖  **Source** — 🤖 Bot means this position has a journal entry (FlowTrader bought it within risk limits); 👤 Manual means no journal record (held before the bot started, or opened directly in Alpaca).  "
                "**Stop** / **Target** / **Score** come from that journal entry.  "
                "**R:R Left** = distance to target ÷ distance to stop from the current price."
            )

        # ═════════════════════════════════════════════════════════════════════════
        # 🪙 BYBIT — Crypto
        # ═════════════════════════════════════════════════════════════════════════
        st.divider()
        bybit_mode = "Testnet" if _bybit().testnet else "Live"
        st.markdown(f"### 🪙 Bybit  —  Crypto ({bybit_mode})  ·  ${bybit_value:,.2f}")

        if not bybit_ok:
            st.info(f"Bybit: {bybit_bal['error']}")
        else:
            cb1, cb2, cb3, cb4 = st.columns(4)
            cb1.metric("Account Value",   f"${bybit_value:,.2f}",
                       help="USDT across both wallets + crypto holdings valued at current spot price.")
            cb2.metric("Free USDT",       f"${bybit_free:,.2f}",
                       help="USDT in the Unified Trading wallet — what the bot can spend on new orders.")
            cb3.metric("Funding USDT",    f"${bybit_funding:,.2f}",
                       help="USDT parked in the Funding account. Transfer to Unified before it can be used to trade.")
            cb4.metric("Invested",        f"${bybit_invested:,.2f}",
                       f"{(bybit_invested/bybit_value*100):.1f}% of account" if bybit_value else "—",
                       help="Value of non-USDT crypto holdings at current spot price.")

            # ── Bot-managed crypto positions only ──────────────────────────────────
            # Re-use the journal lookup we built for Alpaca positions.  Bybit
            # holding `currency` is "BTC"; journal symbol is "BTC/USDT".
            def _find_crypto_entry(currency: str) -> dict:
                for quote in ("USDT", "USDC", "USD"):
                    sym = f"{currency}/{quote}"
                    if sym in entry_by_sym:
                        return entry_by_sym[sym]
                return {}

            # Filter to holdings that have a journal entry — pre-existing
            # testnet/faucet balances aren't real bot trades and only confuse
            # the picture.
            bot_holds = [p for p in bybit_holds if _find_crypto_entry(p.get("currency", ""))]
            n_bot     = len(bot_holds)

            st.markdown(f"**Crypto Positions ({n_bot})**")

            if bot_holds:
                rows = []
                for p in bot_holds:
                    coin     = p.get("currency", "")
                    amount   = float(p.get("amount", 0))
                    cur_px   = float(p["price_usd"]) if p.get("price_usd") else 0
                    val      = float(p.get("value_usd") or 0)
                    pct      = (val / bybit_value * 100) if (val and bybit_value) else None

                    je       = _find_crypto_entry(coin)
                    entry_px = float(je.get("entry_price",   0) or 0)
                    stop     = float(je.get("stop_loss",     0) or 0)
                    target   = float(je.get("take_profit",   0) or 0)
                    score    = je.get("signal_score", "—")

                    pl, plpct = None, None
                    if entry_px > 0 and cur_px > 0:
                        pl    = (cur_px - entry_px) * amount
                        plpct = (cur_px / entry_px - 1) * 100

                    rr_left = "—"
                    if stop > 0 and target > 0 and cur_px > 0:
                        risk   = max(cur_px - stop, 0.0001)
                        reward = max(target - cur_px, 0)
                        rr_left = f"1:{reward/risk:.1f}" if reward > 0 else "0"

                    days_held = "—"
                    ts = je.get("timestamp", "")
                    if ts:
                        try:
                            opened    = _dt_cls.fromisoformat(ts).date()
                            days_held = (_date_cls.today() - opened).days
                        except Exception:
                            pass

                    rows.append({
                        "Asset":     coin,
                        "Amount":    amount,
                        "Entry":     entry_px if entry_px > 0 else None,
                        "Current":   cur_px if cur_px > 0 else None,
                        "Stop":      stop if stop > 0 else None,
                        "Target":    target if target > 0 else None,
                        "Value":     val if val else None,
                        "P&L $":     pl,
                        "P&L %":     plpct,
                        "R:R Left":  rr_left,
                        "Days":      days_held,
                        "Score":     score,
                        "% Account": pct,
                    })
                hdf = pd.DataFrame(rows)

                def _pl_colour(v):
                    if isinstance(v, (int, float)):
                        return "color:#00c853;font-weight:bold" if v >= 0 else "color:#ff5252;font-weight:bold"
                    return ""
                def _cyan(_):   return "color:#26c6da;font-weight:bold"
                def _violet(_): return "color:#b388ff;font-weight:bold"

                st.dataframe(
                    hdf.style
                       .map(_pl_colour, subset=["P&L $", "P&L %"])
                       .map(_cyan,      subset=["Value"])
                       .map(_violet,    subset=["% Account"])
                       .format({
                           "Amount":    "{:,.6f}",
                           "Entry":     "${:,.4f}",
                           "Current":   "${:,.4f}",
                           "Stop":      "${:,.4f}",
                           "Target":    "${:,.4f}",
                           "Value":     "${:,.2f}",
                           "P&L $":     "${:+,.2f}",
                           "P&L %":     "{:+.2f}%",
                           "% Account": "{:.1f}%",
                       }, na_rep="—"),
                    use_container_width=True,
                    hide_index=True,
                )
                # If there are wallet balances the bot didn't open, mention it
                # quietly so the user isn't confused why Account Value > positions shown.
                hidden = len(bybit_holds) - n_bot
                if hidden > 0:
                    st.caption(
                        f"📖  Showing {n_bot} bot-managed position(s).  "
                        f"{hidden} non-bot wallet balance(s) (testnet faucet / manual transfer) hidden — "
                        f"see Wallet split below for the full ledger."
                    )
                else:
                    st.caption(
                        "📖  **Stop / Target / Score** come from the journal entry that opened each position.  "
                        "**R:R Left** = distance to target ÷ distance to stop from the current price."
                    )
            else:
                if bybit_holds:
                    st.caption(
                        "No bot-managed crypto positions yet.  "
                        f"({len(bybit_holds)} wallet balance(s) visible in Account Value are pre-existing — see Wallet split below.)"
                    )
                else:
                    st.caption("No non-USDT holdings.")

            # ── Wallet split (always show if there are any holdings) ────────────
            if bybit_holds:
                with st.expander(f"Wallet split — Unified vs Funding ({len(bybit_holds)} balance(s))"):
                    split_rows = [
                        {
                            "Asset":   p.get("currency"),
                            "Total":   float(p.get("amount", 0)),
                            "Unified": float(p.get("unified", 0)),
                            "Funding": float(p.get("funding", 0)),
                        }
                        for p in bybit_holds
                    ]
                    st.dataframe(
                        pd.DataFrame(split_rows).style.format({
                            "Total":   "{:,.6f}",
                            "Unified": "{:,.6f}",
                            "Funding": "{:,.6f}",
                        }),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.caption(
                        "Raw wallet ledger including pre-existing balances.  "
                        "**Unified** holds tradeable balances; **Funding** must be transferred to Unified before the bot can use it."
                    )

            fetched = bybit_bal.get("fetched_at", "")
            if fetched:
                st.caption(f"Last updated: {fetched[:16].replace('T', ' ')} UTC  ·  refreshed every ~15 min by the trading-bot workflow")

        # ── Day P&L history chart ─────────────────────────────────────────────────
        st.divider()
        st.subheader("Day P&L History (last 14 days)")

        hist = fetch_entries(14)
        pl_by_date: dict = {}
        for e in hist:
            d  = e.get("date", "")
            pl = e.get("day_pl_at_decision")
            if d and pl is not None:
                pl_by_date[d] = pl  # last value for the day wins

        if pl_by_date:
            dates = sorted(pl_by_date)
            vals  = [pl_by_date[d] for d in dates]
            st.plotly_chart(_dark_bar(vals, dates, yprefix="$"), use_container_width=True)
        else:
            st.info("No journal data yet — P&L history will populate after the first bot run.")

        # ── Journal performance summary ───────────────────────────────────────────
        st.divider()
        st.subheader("Performance Summary (last 7 days)")

        week_entries = fetch_entries(7)
        if week_entries:
            filled_trades = [e for e in week_entries if e.get("action") in ["BUY","SELL"]
                             and e.get("execution_status") in ["FILLED","SIMULATED","SUBMITTED"]]
            skips         = [e for e in week_entries if e.get("action") == "SKIP"]
            scores        = [e.get("signal_score", 0) for e in week_entries]

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Total Cycles",   len(week_entries))
            p2.metric("Trades Placed",  len(filled_trades))
            p3.metric("Skips",          len(skips))
            p4.metric("Avg Score",      f"{sum(scores)/len(scores):.1f}/6" if scores else "—")
        else:
            st.info("No journal data for the last 7 days.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — JOURNAL
# ═══════════════════════════════════════════════════════════════════════════════
with tab_journal:

    # ── Filters ───────────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns([1, 2, 3])
    days_back     = f1.selectbox("Period", [7, 14, 30, 90], format_func=lambda d: f"Last {d} days")
    action_filter = f2.multiselect("Action", ["BUY", "SELL", "SKIP", "HOLD"],
                                   default=["BUY", "SELL", "SKIP"])

    all_entries = fetch_entries(days_back)
    filtered    = [e for e in all_entries if e.get("action") in action_filter]
    f3.caption(f"{len(filtered)} of {len(all_entries)} entries shown")

    if not filtered:
        st.info("No journal entries yet. Run `python main.py full` to generate entries.")
    else:
        # ── Summary metrics ───────────────────────────────────────────────────
        trades    = [e for e in filtered if e.get("action") in ["BUY", "SELL"]]
        skips     = [e for e in filtered if e.get("action") == "SKIP"]
        filled    = [e for e in trades   if e.get("execution_status") in ["FILLED","SIMULATED","SUBMITTED"]]
        avg_score = sum(e.get("signal_score", 0) for e in filtered) / len(filtered)
        skip_rate = len(skips) / len(filtered) * 100 if filtered else 0

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total Cycles",     len(filtered))
        m2.metric("Trades Placed",    len(trades))
        m3.metric("Orders Executed",  len(filled))
        m4.metric("Skips",            len(skips))
        m5.metric("Skip Rate",        f"{skip_rate:.0f}%")

        st.divider()

        # ── P&L by Day ────────────────────────────────────────────────────────
        st.subheader("P&L by Day")

        # Build day-level summary from all entries (not just filtered action types)
        pl_by_day: dict[str, dict] = {}
        for e in all_entries:
            d  = e.get("date", "")
            pl = e.get("day_pl_at_decision")
            if not d:
                continue
            if d not in pl_by_day:
                pl_by_day[d] = {"pl": 0.0, "trades": 0, "skips": 0}
            if pl is not None:
                pl_by_day[d]["pl"] = float(pl)   # last value per day wins
            if e.get("action") in ["BUY", "SELL"]:
                pl_by_day[d]["trades"] += 1
            elif e.get("action") == "SKIP":
                pl_by_day[d]["skips"] += 1

        if pl_by_day:
            sorted_days = sorted(pl_by_day, reverse=True)

            # Bar chart
            bar_vals  = [pl_by_day[d]["pl"] for d in sorted(pl_by_day)]
            bar_dates = sorted(pl_by_day)
            st.plotly_chart(_dark_bar(bar_vals, bar_dates, yprefix="$", height=180),
                            use_container_width=True)

            # Weekly rollup above the table
            from datetime import date as _date
            week_totals: dict[str, float] = {}
            for d, v in pl_by_day.items():
                try:
                    iso = _date.fromisoformat(d)
                    week_key = iso.strftime("W%V %Y")   # e.g. "W19 2026"
                    week_totals[week_key] = week_totals.get(week_key, 0.0) + v["pl"]
                except ValueError:
                    pass

            if week_totals:
                sorted_weeks = sorted(week_totals)[-4:]   # last 4 weeks
                wc = st.columns(len(sorted_weeks))
                for i, wk in enumerate(sorted_weeks):
                    val = week_totals[wk]
                    wc[i].metric(wk, f"${val:+,.2f}")

            # Daily table
            day_rows = []
            for d in sorted_days:
                v  = pl_by_day[d]
                pl = v["pl"]
                day_rows.append({
                    "Date":    d,
                    "Day P&L": f"${pl:+,.2f}",
                    "Trades":  v["trades"],
                    "Skips":   v["skips"],
                    "Result":  "✅ Profit" if pl > 0 else ("🔴 Loss" if pl < 0 else "➖ Flat"),
                })

            day_df = pd.DataFrame(day_rows)

            def _pl_colour(v: str):
                if v.startswith("$+") or (v.startswith("$") and not v.startswith("$-") and v != "$+0.00"):
                    return "color:#00c853;font-weight:bold"
                if v.startswith("$-"):
                    return "color:#ff5252;font-weight:bold"
                return "color:#888"

            st.dataframe(
                day_df.style.map(_pl_colour, subset=["Day P&L"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No P&L data yet — populates after the first bot run during market hours.")

        st.divider()

        # ── Charts row ────────────────────────────────────────────────────────
        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.caption("Signal Score Distribution")
            score_counts = {}
            for e in filtered:
                s = e.get("signal_score", 0)
                score_counts[s] = score_counts.get(s, 0) + 1
            if score_counts:
                sc_fig = go.Figure(go.Bar(
                    x=list(score_counts.keys()),
                    y=list(score_counts.values()),
                    marker_color=["#00c853" if k >= 5 else "#ffab00" if k >= 3 else "#ff5252"
                                  for k in score_counts],
                    marker_line_width=0,
                ))
                sc_fig.update_layout(
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", font_color="#fafafa",
                    height=200, margin=dict(l=0, r=0, t=4, b=0),
                    xaxis=dict(title="Score", showgrid=False, tickmode="linear"),
                    yaxis=dict(title="Count", gridcolor="#1c1f26"),
                )
                st.plotly_chart(sc_fig, use_container_width=True)

        with chart_col2:
            st.caption("Trade vs Skip Breakdown")
            label_map  = {"BUY": "Buy", "SELL": "Sell", "SKIP": "Skip", "HOLD": "Hold"}
            pie_counts = {}
            for e in filtered:
                a = label_map.get(e.get("action","SKIP"), "Skip")
                pie_counts[a] = pie_counts.get(a, 0) + 1
            if pie_counts:
                pie_fig = go.Figure(go.Pie(
                    labels=list(pie_counts.keys()),
                    values=list(pie_counts.values()),
                    hole=0.55,
                    marker_colors=["#40916c","#f72585","#555","#ffab00"],
                    textinfo="label+percent",
                    textposition="outside",
                ))
                pie_fig.update_layout(
                    paper_bgcolor="#0e1117", font_color="#fafafa",
                    height=260, margin=dict(l=20, r=20, t=20, b=20),
                    showlegend=False,
                )
                st.plotly_chart(pie_fig, use_container_width=True)

        st.divider()

        # ── Trade log (trades only) ───────────────────────────────────────────
        _BLANK_SYMS = {None, "", "N/A", "NONE", "None", "null", "none"}

        def _clean_sym(v):
            return v if v not in _BLANK_SYMS else "—"

        def _fp(v):
            """Pre-format price — never passes None/0 to pandas."""
            if v is None or v == 0:
                return "—"
            try:
                return f"${float(v):,.2f}"
            except (TypeError, ValueError):
                return "—"

        def _fpl(v):
            """Format P&L with sign and colour hint in string."""
            if v is None:
                return "—"
            try:
                f = float(v)
                return f"${f:+,.2f}"
            except (TypeError, ValueError):
                return "—"

        trade_entries = [e for e in reversed(filtered) if e.get("action") in ["BUY","SELL"]]
        skip_entries  = [e for e in reversed(filtered) if e.get("action") == "SKIP"]

        st.subheader("Trade Log")
        if trade_entries:
            trade_rows = []
            for e in trade_entries:
                trade_rows.append({
                    "Date":       e.get("date", ""),
                    "Time":       e.get("time_est", "").split(".")[0],
                    "Action":     e.get("action", ""),
                    "Symbol":     _clean_sym(e.get("symbol")),
                    "Score":      e.get("signal_score", 0),
                    "Entry $":    _fp(e.get("entry_price")),
                    "Stop $":     _fp(e.get("stop_loss")),
                    "Target $":   _fp(e.get("take_profit")),
                    "R:R":        e.get("risk_reward") or "—",
                    "Exec":       e.get("execution_status", "—"),
                    "Day P&L":    _fpl(e.get("day_pl_at_decision")),
                    "Confidence": e.get("confidence", "—"),
                })
            tdf = pd.DataFrame(trade_rows)
            st.dataframe(
                tdf.style
                   .map(_action_colour, subset=["Action"])
                   .map(_exec_colour,   subset=["Exec"])
                   .map(_score_colour,  subset=["Score"])
                   .map(_pl_colour,     subset=["Day P&L"]),
                use_container_width=True,
                hide_index=True,
            )
            st.caption("ℹ️ Per-trade exit P&L requires position monitoring — Day P&L shows the account's daily result at decision time.")
        else:
            st.info("No trades placed in this period. The bot is scanning and skipping until a setup meets the signal threshold.")

        # ── All activity (collapsible) ────────────────────────────────────────
        with st.expander(f"All bot activity — {len(filtered)} entries (including skips)"):
            all_rows = []
            for e in list(reversed(filtered)):
                all_rows.append({
                    "Date":    e.get("date", ""),
                    "Time":    e.get("time_est", "").split(".")[0],
                    "Action":  e.get("action", ""),
                    "Symbol":  _clean_sym(e.get("symbol")),
                    "Score":   e.get("signal_score", 0),
                    "Entry $": _fp(e.get("entry_price")),
                    "Stop $":  _fp(e.get("stop_loss")),
                    "Target $":_fp(e.get("take_profit")),
                    "Exec":    e.get("execution_status", "—"),
                    "Day P&L": _fpl(e.get("day_pl_at_decision")),
                })
            adf = pd.DataFrame(all_rows)
            st.dataframe(
                adf.style
                   .map(_action_colour, subset=["Action"])
                   .map(_exec_colour,   subset=["Exec"])
                   .map(_score_colour,  subset=["Score"])
                   .map(_pl_colour,     subset=["Day P&L"]),
                use_container_width=True,
                hide_index=True,
            )

        # ── Entry inspector ───────────────────────────────────────────────────
        st.divider()
        st.subheader("Entry Inspector")
        rev    = list(reversed(filtered))
        labels = [
            f"{e.get('date')} {e.get('time_est','').split('.')[0]}  |  "
            f"{e.get('action')} {e.get('symbol') or ''}  |  "
            f"Score {e.get('signal_score',0)}/6  |  {e.get('execution_status','—')}"
            for e in rev
        ]
        idx = st.selectbox("Select entry", range(len(labels)),
                           format_func=lambda i: labels[i])
        sel = rev[idx]

        insp1, insp2 = st.columns(2)

        with insp1:
            st.markdown("**Decision Details**")
            dec_keys = [
                "action","symbol","signal_score","signals_fired",
                "confidence","entry_price","stop_loss","take_profit",
                "quantity","risk_reward","execution_status","rejection_reason"
            ]
            st.json({k: sel[k] for k in dec_keys if k in sel})

            st.markdown("**Market Context at Decision Time**")
            ctx_keys = [
                "account_value","open_positions","day_pl_at_decision",
                "top_setup_symbol","top_setup_score","paper_trade"
            ]
            st.json({k: sel[k] for k in ctx_keys if k in sel})

        with insp2:
            st.markdown("**Claude's Reasoning**")
            st.text_area(
                "reasoning",
                value=sel.get("reasoning", "No reasoning recorded."),
                height=220,
                disabled=True,
                label_visibility="collapsed",
            )
            st.markdown("**Journal Entry**")
            st.text_area(
                "journal",
                value=sel.get("journal_entry", "—"),
                height=160,
                disabled=True,
                label_visibility="collapsed",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — RESEARCH (Weekly Analyst Memo)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_research:

    memo = load_research_memo()

    if not memo:
        st.info(
            "**No research memo yet.**  "
            "The Research Analyst runs automatically every **Sunday and Wednesday at 18:00 EDT** via GitHub Actions, "
            "and writes a fresh memo to `journal/weekly_research_memo.json`.  "
            "To run it manually right now from your terminal:  `python main.py research-analyst`"
        )
    else:
        # ── Helpers ────────────────────────────────────────────────────────────
        def _txt(v, default: str = "—") -> str:
            """Coerce arbitrary memo values to a displayable string."""
            if v is None or v == "":
                return default
            if isinstance(v, (str, int, float, bool)):
                return str(v)
            if isinstance(v, dict):
                # Prefer human-readable keys if present
                for k in ("text", "description", "detail", "reason", "warning", "trend_or_range"):
                    if k in v:
                        return str(v[k])
                return json.dumps(v, ensure_ascii=False)[:300]
            if isinstance(v, list):
                return ", ".join(_txt(x) for x in v) or default
            return str(v)

        def _severity_style(sev: str) -> tuple[str, str]:
            """Return (emoji, severity-tag) for a severity string."""
            s = (sev or "").upper()
            if s in ("CRITICAL",):     return "🔴", "CRITICAL"
            if s in ("HIGH",):         return "🟠", "HIGH"
            if s in ("MEDIUM", "MED"): return "🟡", "MEDIUM"
            if s in ("LOW",):          return "🔵", "LOW"
            return "⚪", s or "INFO"

        gen_at_raw   = memo.get("generated_at", "")
        valid_to_raw = memo.get("valid_until", "")
        gen_at       = gen_at_raw[:16].replace("T", " ") if gen_at_raw else "—"
        valid_to     = valid_to_raw[:10] if valid_to_raw else "—"

        # ── Page title strip ──────────────────────────────────────────────────
        title_l, title_r = st.columns([5, 1])
        with title_l:
            st.markdown("## 🧠 Weekly Research Memo")
            crypto_present = bool(memo.get("crypto_outlook"))
            scope_tag = "Equities + Crypto" if crypto_present else "Equities only"
            st.caption(
                f"Generated **{gen_at}**  ·  Valid until **{valid_to}**  ·  "
                f"Coverage: **{scope_tag}**  ·  Refreshed every Sun + Wed at 18:00 EDT"
            )
        with title_r:
            if st.button("⟳ Reload memo", help="Force-reload the memo from disk; useful right after a sync run"):
                load_research_memo.clear()
                st.rerun()

        if memo.get("expired"):
            st.warning("⚠️ This memo has passed its valid-until date. The next scheduled run will refresh it.")

        # ── Top header band: 3 status cards ───────────────────────────────────
        confidence  = int(memo.get("confidence_score", 0) or 0)
        raw_regime  = memo.get("market_regime") or {}
        if not isinstance(raw_regime, dict):
            raw_regime = {"trend_or_range": str(raw_regime)}
        mr_active   = bool(raw_regime.get("mean_reversion_active", True))
        regime_txt  = _txt(raw_regime.get("trend_or_range", "Unknown"))

        # Trade posture is derived from the confidence score
        if confidence >= 7:
            posture_label, posture_detail = "🟢 Normal", "Standard sizing.  Trade A/B/C grade setups."
        elif confidence >= 5:
            posture_label, posture_detail = "🟡 Selective", "Prefer A-grade setups.  Consider half-size on B-grade."
        elif confidence >= 3:
            posture_label, posture_detail = "🟠 Cautious", "A-grade only.  Half normal size."
        else:
            posture_label, posture_detail = "🔴 Defensive", "Stand aside or A-grade only at quarter size."

        c1, c2, c3 = st.columns(3)
        with c1:
            with st.container(border=True):
                st.markdown("**Trading Confidence**")
                st.markdown(f"<div style='font-size:2.4rem;font-weight:700;line-height:1'>{confidence}<span style='font-size:1.2rem;color:#888'> / 10</span></div>", unsafe_allow_html=True)
                st.progress(confidence / 10)
        with c2:
            with st.container(border=True):
                st.markdown("**Mean Reversion**")
                if mr_active:
                    st.markdown("<div style='font-size:1.6rem;font-weight:700;color:#40916c'>▶ ACTIVE</div>", unsafe_allow_html=True)
                    st.caption("Strategy is engaged — bot will take qualifying setups.")
                else:
                    st.markdown("<div style='font-size:1.6rem;font-weight:700;color:#ffab00'>⏸ PAUSED</div>", unsafe_allow_html=True)
                    st.caption("Strategy on hold — research conditions don't support entries.")
        with c3:
            with st.container(border=True):
                st.markdown("**Trade Posture**")
                st.markdown(f"<div style='font-size:1.4rem;font-weight:700'>{posture_label}</div>", unsafe_allow_html=True)
                st.caption(posture_detail)

        # ── What this means (plain-English summary) ───────────────────────────
        reason = _txt(memo.get("confidence_reason", ""))
        if reason and reason != "—":
            st.markdown("##### 📋 What this means for the bot this week")
            st.info(reason)

        st.divider()

        # ── Market conditions detail ──────────────────────────────────────────
        st.markdown("### 🌐 Market Conditions")

        # Build labelled rows from whatever fields the memo provides
        rows = []
        if regime_txt and regime_txt != "—":
            rows.append(("Regime",         regime_txt))
        vix_read = _txt(raw_regime.get("vix_interpretation", ""))
        if vix_read and vix_read != "—":
            rows.append(("VIX Read",       vix_read))
        sizing = _txt(raw_regime.get("vix_position_sizing_guidance", raw_regime.get("position_sizing_guidance", "")))
        if sizing and sizing != "—":
            rows.append(("Sizing Guidance", sizing))
        mr_rec = _txt(raw_regime.get("mean_reversion_recommendation", ""))
        if mr_rec and mr_rec != "—":
            rows.append(("Recommendation", mr_rec))

        if rows:
            for label, value in rows:
                lc, vc = st.columns([1, 4])
                lc.markdown(f"**{label}**")
                vc.markdown(value)
        else:
            st.caption("No market-conditions detail was recorded in this memo.")

        st.divider()

        # ── Crypto Outlook ────────────────────────────────────────────────────
        crypto_outlook = memo.get("crypto_outlook") or {}
        if crypto_outlook:
            st.markdown("### 🪙 Crypto Outlook")
            st.caption("BTC dominance, market sentiment, and crypto-specific regime read.")

            mr_active_c = bool(crypto_outlook.get("mean_reversion_active_crypto", True))
            regime_c    = _txt(crypto_outlook.get("regime", "Unknown"))

            cc1, cc2 = st.columns(2)
            with cc1:
                with st.container(border=True):
                    st.markdown("**Crypto Regime**")
                    st.markdown(f"<div style='font-size:1.4rem;font-weight:700'>{regime_c}</div>", unsafe_allow_html=True)
                    if mr_active_c:
                        st.caption("✅ Mean reversion is engaged for crypto pairs.")
                    else:
                        st.caption("⏸ Mean reversion paused — research conditions don't support crypto entries.")
            with cc2:
                with st.container(border=True):
                    st.markdown("**Mean Reversion (Crypto)**")
                    if mr_active_c:
                        st.markdown("<div style='font-size:1.6rem;font-weight:700;color:#40916c'>▶ ACTIVE</div>", unsafe_allow_html=True)
                    else:
                        st.markdown("<div style='font-size:1.6rem;font-weight:700;color:#ffab00'>⏸ PAUSED</div>", unsafe_allow_html=True)
                    st.caption("Whether the crypto book is allowed to take new mean-reversion entries this week.")

            crypto_rows = []
            sentiment_read = _txt(crypto_outlook.get("sentiment_read", ""))
            dominance_read = _txt(crypto_outlook.get("dominance_read", ""))
            if sentiment_read and sentiment_read != "—":
                crypto_rows.append(("Sentiment Read", sentiment_read))
            if dominance_read and dominance_read != "—":
                crypto_rows.append(("Dominance Read", dominance_read))
            for label, value in crypto_rows:
                lc, vc = st.columns([1, 4])
                lc.markdown(f"**{label}**")
                vc.markdown(value)

            # Top crypto opportunities (separate from equity ones)
            crypto_opps = crypto_outlook.get("top_crypto_opportunities") or []
            if crypto_opps:
                st.markdown("**🎯 Top Crypto Opportunities**")
                for i, opp in enumerate(crypto_opps, start=1):
                    if isinstance(opp, dict):
                        sym         = opp.get("symbol", opp.get("pair", "?"))
                        rationale   = _txt(opp.get("rationale", opp.get("reason", "")))
                        direction   = _txt(opp.get("direction", ""))
                        strength    = _txt(opp.get("signal_strength", opp.get("confidence", "")))
                        entry_cond  = _txt(opp.get("entry_condition", ""))
                        max_pct     = opp.get("max_position_size_pct_of_crypto_book")
                        bits = [f"#{i}", f"**{sym}**"]
                        if strength and strength != "—":
                            bits.append(f"signal: {strength[:25]}")
                        if direction and direction != "—":
                            bits.append(f"direction: {direction[:25]}")
                        with st.expander("  ·  ".join(bits)):
                            if rationale and rationale != "—":
                                st.markdown(f"**Rationale**  \n{rationale}")
                            if entry_cond and entry_cond != "—":
                                st.markdown(f"**Entry Condition**  \n{entry_cond}")
                            if max_pct is not None:
                                st.markdown(f"**Max position size:** {max_pct}% of the crypto book")
                    else:
                        st.markdown(f"- #{i} — {_txt(opp)}")

            # Crypto-specific risk warnings
            crypto_risks = crypto_outlook.get("crypto_risk_warnings") or []
            if crypto_risks:
                st.markdown("**⚠️ Crypto-Specific Risks**")
                for r in crypto_risks:
                    if isinstance(r, dict):
                        emoji, sev_tag = _severity_style(r.get("severity", ""))
                        title  = r.get("event", r.get("title", "Risk"))
                        detail = _txt(r.get("detail", r.get("description", "")))
                        header = f"{emoji}  **{sev_tag}**  ·  {title}"
                        if str(r.get("severity", "")).upper() == "CRITICAL":
                            st.error(header + "  \n" + detail)
                        elif str(r.get("severity", "")).upper() == "HIGH":
                            st.warning(header + "  \n" + detail)
                        else:
                            st.info(header + "  \n" + detail)
                    else:
                        st.info(_txt(r))

            st.divider()

        # ── Top opportunities ─────────────────────────────────────────────────
        opportunities = memo.get("top_opportunities", []) or []
        st.markdown(f"### 🎯 Top Opportunities  ·  {len(opportunities)} flagged")
        st.caption("Per-symbol setups the analyst flagged for this week. Expand each to read the full rationale.")

        if not opportunities:
            st.info("No specific opportunities flagged this week.")
        else:
            for i, opp in enumerate(opportunities, start=1):
                if isinstance(opp, dict):
                    sym       = opp.get("symbol", "?")
                    rank      = opp.get("rank", i)
                    direction = _txt(opp.get("direction", ""))
                    strength  = _txt(opp.get("signal_strength", opp.get("score", "")))
                    rationale = _txt(opp.get("rationale", opp.get("reason", opp.get("why", ""))))
                    sizing_n  = _txt(opp.get("position_size_note", ""))

                    # Header line for the expander label — short summary
                    bits = [f"#{rank}", f"**{sym}**"]
                    if strength and strength != "—":
                        bits.append(f"signal: {strength}")
                    if direction and direction != "—":
                        bits.append(f"direction: {direction[:40]}{'…' if len(direction) > 40 else ''}")
                    label = "  ·  ".join(bits)

                    with st.expander(label, expanded=(i == 1)):
                        if rationale and rationale != "—":
                            st.markdown(f"**Rationale**  \n{rationale}")
                        if direction and direction != "—":
                            st.markdown(f"**Direction**  \n{direction}")
                        if sizing_n and sizing_n != "—":
                            st.markdown(f"**Position size note**  \n{sizing_n}")
                else:
                    st.markdown(f"- #{i}  ·  {_txt(opp)}")

        st.divider()

        # ── Watchlist changes ─────────────────────────────────────────────────
        wl_changes = memo.get("watchlist_changes") or {}
        adds       = wl_changes.get("add", []) or []
        reduces    = wl_changes.get("remove_or_reduce", wl_changes.get("remove", [])) or []
        avoid_earn = wl_changes.get("avoid_earnings", []) or []

        st.markdown("### 🔄 Watchlist Changes")
        st.caption("The analyst's recommendations on what to add, remove, or temporarily avoid.")

        wc1, wc2 = st.columns(2)
        with wc1:
            st.markdown("**➕ Add**")
            if adds:
                for item in adds:
                    if isinstance(item, dict):
                        sym = item.get("symbol", "?")
                        rsn = _txt(item.get("reason", ""))
                        st.success(f"**{sym}** — {rsn}")
                    else:
                        st.success(f"**{_txt(item)}**")
            else:
                st.caption("No additions recommended.")
        with wc2:
            st.markdown("**➖ Reduce / Remove**")
            if reduces:
                for item in reduces:
                    if isinstance(item, dict):
                        sym    = item.get("symbol", "?")
                        rsn    = _txt(item.get("reason", ""))
                        action = _txt(item.get("action", ""))
                        line   = f"**{sym}** — {rsn}"
                        if action and action != "—":
                            line += f"  \n*Action: {action}*"
                        st.warning(line)
                    else:
                        st.warning(f"**{_txt(item)}**")
            else:
                st.caption("No reductions recommended.")

        if avoid_earn:
            st.markdown("**⚠️ Avoid this week (earnings)**")
            for item in avoid_earn:
                if isinstance(item, dict):
                    sym = item.get("symbol", "?")
                    rsn = _txt(item.get("reason", ""))
                    st.error(f"**{sym}** — {rsn}")
                else:
                    st.error(f"**{_txt(item)}**")

        st.divider()

        # ── Sector focus ──────────────────────────────────────────────────────
        sector_focus = memo.get("sector_focus") or {}
        st.markdown("### 🏭 Sector Focus")
        st.caption("Sectors the analyst sees as best-suited or least-suited for mean-reversion entries this week.")

        favour_list = (sector_focus.get("best_mean_reversion_sectors")
                       or sector_focus.get("favour")
                       or sector_focus.get("best")
                       or [])
        avoid_list  = (sector_focus.get("worst_mean_reversion_sectors")
                       or sector_focus.get("avoid")
                       or sector_focus.get("worst")
                       or [])

        sf1, sf2 = st.columns(2)
        with sf1:
            st.markdown("**✅ Favour**")
            if favour_list:
                for s in favour_list:
                    if isinstance(s, dict):
                        sec   = s.get("sector", s.get("etf", "?"))
                        etf   = s.get("etf", "")
                        rsn   = _txt(s.get("reasoning", s.get("reason", "")))
                        cond  = _txt(s.get("condition", ""))
                        title = f"{sec}" + (f" ({etf})" if etf and etf not in sec else "")
                        block = f"**{title}**  \n{rsn}"
                        if cond and cond != "—":
                            block += f"  \n*Condition: {cond}*"
                        st.success(block)
                    else:
                        st.success(_txt(s))
            else:
                st.caption("No sectors flagged.")
        with sf2:
            st.markdown("**⛔ Avoid / Underweight**")
            if avoid_list:
                for s in avoid_list:
                    if isinstance(s, dict):
                        sec   = s.get("sector", s.get("etf", "?"))
                        etf   = s.get("etf", "")
                        rsn   = _txt(s.get("reasoning", s.get("reason", "")))
                        cond  = _txt(s.get("condition", ""))
                        title = f"{sec}" + (f" ({etf})" if etf and etf not in sec else "")
                        block = f"**{title}**  \n{rsn}"
                        if cond and cond != "—":
                            block += f"  \n*Condition: {cond}*"
                        st.error(block)
                    else:
                        st.error(_txt(s))
            else:
                st.caption("No sectors flagged.")

        st.divider()

        # ── Risk warnings ─────────────────────────────────────────────────────
        st.markdown("### ⚠️ Risk Warnings")
        st.caption("Macro, geopolitical, or technical risks the bot should account for this week.")

        rw       = memo.get("risk_warnings") or {}
        warnings: list = []

        if isinstance(rw, dict):
            # Flatten all categories into one list of {category, event/title, severity, detail}
            for category, items in rw.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            warnings.append({
                                "category": category.replace("_", " ").title(),
                                "title":    item.get("event", item.get("title", item.get("warning", "Risk"))),
                                "severity": item.get("severity", item.get("priority", "")),
                                "detail":   _txt(item.get("detail", item.get("description", item.get("text", "")))),
                            })
                        else:
                            warnings.append({
                                "category": category.replace("_", " ").title(),
                                "title":    "Note",
                                "severity": "",
                                "detail":   _txt(item),
                            })
                elif items:
                    warnings.append({
                        "category": category.replace("_", " ").title(),
                        "title":    "Note",
                        "severity": "",
                        "detail":   _txt(items),
                    })
        elif isinstance(rw, list):
            for item in rw:
                if isinstance(item, dict):
                    warnings.append({
                        "category": item.get("category", "Risk"),
                        "title":    item.get("event", item.get("title", item.get("warning", "Risk"))),
                        "severity": item.get("severity", item.get("priority", "")),
                        "detail":   _txt(item.get("detail", item.get("description", item.get("text", "")))),
                    })
                else:
                    warnings.append({"category": "Risk", "title": "Note", "severity": "", "detail": _txt(item)})

        # Sort by severity rank: CRITICAL > HIGH > MEDIUM > LOW > others
        sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "MED": 2, "LOW": 3}
        warnings.sort(key=lambda w: sev_rank.get(str(w["severity"]).upper(), 9))

        if not warnings:
            st.success("✅ No significant risk warnings for this week.")
        else:
            for w in warnings:
                emoji, sev_tag = _severity_style(w["severity"])
                header = f"{emoji}  **{sev_tag}**  ·  {w['title']}  ·  *{w['category']}*"
                if str(w["severity"]).upper() == "CRITICAL":
                    st.error(header + "  \n" + w["detail"])
                elif str(w["severity"]).upper() == "HIGH":
                    st.warning(header + "  \n" + w["detail"])
                else:
                    st.info(header + "  \n" + w["detail"])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — LEARN
# ═══════════════════════════════════════════════════════════════════════════════
with tab_learn:
    import numpy as np

    st.markdown("## 📚 FlowTrader — Trading Guide")
    st.caption("Everything you need to understand what FlowTrader is doing and why. No prior trading experience needed.")

    learn_tabs = st.tabs([
        "🎯 How It Decides", "📊 Indicators", "📈 Asset Classes", "🛡️ Risk Rules", "🔍 Reading the Dashboard"
    ])

    # ── SUB-TAB 1: How It Decides ─────────────────────────────────────────────
    with learn_tabs[0]:
        st.subheader("How FlowTrader Makes a Trade Decision")
        st.markdown("""
FlowTrader uses a **mean reversion** strategy. The core idea is simple:

> *Prices that move too far from their average tend to snap back.*

When a stock drops sharply and looks oversold, FlowTrader buys expecting it to recover.
It never chases momentum — it waits for things to be "on sale."
        """)

        st.divider()
        st.subheader("The Signal Score System (0 → 6)")
        st.markdown("Every symbol gets scored each cycle. **Need 3 or more signals to even consider a trade.**")

        score_data = {
            "Signal": ["RSI < 32 (strongly oversold)", "RSI < 40 (mildly oversold)", "Price below lower Bollinger Band",
                       "Price below VWAP by > 1%", "ADX < 20 (ranging market)", "Positive news sentiment"],
            "Points": ["+2", "+1", "+1", "+1", "+1", "+1"],
            "What it means": [
                "Price dropped hard — likely oversold, bounce expected",
                "Price dipped — moderate oversold signal",
                "Price is statistically cheap relative to recent range",
                "Price is below where most volume traded — discount zone",
                "Market is calm and sideways — mean reversion works here",
                "News is positive for the asset — tailwind for recovery",
            ],
        }
        st.dataframe(score_data, use_container_width=True, hide_index=True)

        st.divider()

        cols = st.columns(4)
        for score, label, color, desc in [
            (0, "NO TRADE", "#555", "Not enough signals"),
            (3, "C-GRADE", "#ffd60a", "Minimum — trade with caution"),
            (4, "B-GRADE", "#4cc9f0", "Good setup"),
            (5, "A-GRADE", "#40916c", "Best setup — full size"),
        ]:
            cols[["NO TRADE","C-GRADE","B-GRADE","A-GRADE"].index(label)].markdown(
                f"<div style='background:#1c1f26;border-radius:10px;padding:14px;text-align:center;'>"
                f"<div style='font-size:2rem;font-weight:700;color:{color}'>{score}/6</div>"
                f"<div style='color:{color};font-weight:600'>{label}</div>"
                f"<div style='color:#888;font-size:0.8rem;margin-top:6px'>{desc}</div></div>",
                unsafe_allow_html=True
            )

        st.divider()
        st.subheader("The Full Decision Flow")
        st.markdown("""
```
Every 30 minutes during market hours:

1. Fetch live prices & indicators for all 13 watchlist symbols
2. Score each symbol (0–6 signals)
3. Check market regime → ADX > 25? SKIP (trending market)
4. Best scoring symbol sent to Claude AI for analysis
5. Claude decides: BUY / SELL / SKIP
6. Order placed via Alpaca (paper or live)
7. Result logged to Journal
```
        """)
        st.info("**Key rule:** FlowTrader will SKIP a trade if the market is trending strongly (ADX > 25), even if RSI looks oversold. Trending markets don't revert — they keep going.")

    # ── SUB-TAB 2: Indicators ────────────────────────────────────────────────
    with learn_tabs[1]:

        ind_tabs = st.tabs(["RSI", "Bollinger Bands", "ADX & Regime", "VWAP", "ATR", "Moving Averages"])

        # RSI
        with ind_tabs[0]:
            st.subheader("RSI — Relative Strength Index")
            st.markdown("""
**Range: 0 to 100**

RSI measures how fast and how much a price has moved recently. It tells you whether something is "overdone" in either direction.
            """)
            rsi_fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=28,
                title={"text": "Example RSI — Oversold Signal", "font": {"color": "#fafafa"}},
                gauge={
                    "axis": {"range": [0, 100], "tickcolor": "#fafafa"},
                    "bar": {"color": "#4cc9f0"},
                    "bgcolor": "#1c1f26",
                    "steps": [
                        {"range": [0, 32],  "color": "#1b4332"},
                        {"range": [32, 40], "color": "#2d3a1e"},
                        {"range": [40, 60], "color": "#1c2030"},
                        {"range": [60, 68], "color": "#3b2a1e"},
                        {"range": [68, 100],"color": "#3b1f2b"},
                    ],
                    "threshold": {"line": {"color": "#40916c", "width": 3}, "thickness": 0.75, "value": 32},
                },
            ))
            rsi_fig.update_layout(paper_bgcolor="#0e1117", font_color="#fafafa", height=280, margin=dict(t=40,b=0))
            st.plotly_chart(rsi_fig, use_container_width=True)

            r1, r2, r3 = st.columns(3)
            r1.error("**RSI > 68 — Overbought**\nAsset ran up too fast. Likely to pull back. Avoid buying.")
            r2.info("**RSI 40–60 — Neutral**\nNo clear signal. FlowTrader waits.")
            r3.success("**RSI < 32 — Oversold ✅**\nAsset dropped hard. Likely to bounce. +2 signal points — strongest single signal.")
            st.caption("RSI below 40 adds +1 point. RSI below 32 adds +2 points (double weight — it's a strong signal).")

        # Bollinger Bands
        with ind_tabs[1]:
            st.subheader("Bollinger Bands & BB %B")
            st.markdown("""
**Bollinger Bands** draw a price channel around a moving average using standard deviation.
About 95% of price action stays *inside* the bands — when price breaks outside, it's statistically extreme.

**BB %B** tells you WHERE price is inside that channel:
- **1.0** = at the upper band (expensive)
- **0.5** = at the middle (the average)
- **0.0** = at the lower band (cheap)
- **Negative** = *below* the lower band (very cheap — strong signal)
            """)
            x = np.linspace(0, 60, 61)
            np.random.seed(42)
            prices = 100 + np.cumsum(np.random.randn(61) * 0.8)
            ma = pd.Series(prices).rolling(20, min_periods=1).mean().values
            std = pd.Series(prices).rolling(20, min_periods=1).std().fillna(1).values
            upper = ma + 2 * std
            lower = ma - 2 * std

            bb_fig = go.Figure()
            bb_fig.add_trace(go.Scatter(x=x, y=upper, name="Upper Band", line=dict(color="#f72585", width=1, dash="dash")))
            bb_fig.add_trace(go.Scatter(x=x, y=lower, name="Lower Band", line=dict(color="#40916c", width=1, dash="dash"),
                                        fill="tonexty", fillcolor="rgba(28,31,38,0.4)"))
            bb_fig.add_trace(go.Scatter(x=x, y=ma, name="MA20 (mean)", line=dict(color="#4cc9f0", width=1.5)))
            bb_fig.add_trace(go.Scatter(x=x, y=prices, name="Price", line=dict(color="#fafafa", width=2)))
            bb_fig.add_annotation(x=x[-1], y=lower[-1], text="← BUY zone", showarrow=False, font=dict(color="#40916c", size=12), xanchor="left")
            bb_fig.update_layout(paper_bgcolor="#0e1117", plot_bgcolor="#0e1117", font_color="#fafafa",
                                 height=280, margin=dict(l=0,r=0,t=8,b=0),
                                 yaxis=dict(gridcolor="#1c1f26"), xaxis=dict(showgrid=False, title="Days"),
                                 legend=dict(orientation="h"))
            st.plotly_chart(bb_fig, use_container_width=True)
            st.success("**FlowTrader signal fires when price touches or breaks below the lower band.** That's when BB %B ≈ 0 or negative.")

        # ADX & Regime
        with ind_tabs[2]:
            st.subheader("ADX — Average Directional Index & Market Regime")
            st.markdown("""
ADX measures **how strongly a market is trending** — not which direction, just how strong.

This is critical because **mean reversion only works in calm, sideways markets**.
In a strong trend, prices don't snap back — they keep going.
            """)
            adx_fig = go.Figure()
            adx_vals = [5, 10, 15, 18, 20, 22, 25, 28, 32, 38, 45]
            adx_labels = ["5","10","15","18","20","22","25","28","32","38","45"]
            adx_colors = ["#40916c" if v < 20 else "#ffd60a" if v < 25 else "#ff5252" for v in adx_vals]
            adx_fig.add_trace(go.Bar(x=adx_labels, y=adx_vals, marker_color=adx_colors, marker_line_width=0))
            adx_fig.add_hline(y=20, line_dash="dash", line_color="#40916c", annotation_text="ADX 20 — good for mean reversion", annotation_font_color="#40916c")
            adx_fig.add_hline(y=25, line_dash="dash", line_color="#ff5252", annotation_text="ADX 25 — trend filter kicks in (SKIP)", annotation_font_color="#ff5252")
            adx_fig.update_layout(paper_bgcolor="#0e1117", plot_bgcolor="#0e1117", font_color="#fafafa",
                                  height=260, margin=dict(l=0,r=0,t=8,b=0),
                                  yaxis=dict(gridcolor="#1c1f26", title="ADX Value"), xaxis=dict(showgrid=False))
            st.plotly_chart(adx_fig, use_container_width=True)

            a1, a2, a3 = st.columns(3)
            a1.success("**ADX < 20**\n🟢 RANGING\nMean reversion active\n+1 signal point")
            a2.warning("**ADX 20–25**\n🟡 BORDERLINE\nProceed with caution\nNo bonus point")
            a3.error("**ADX > 25**\n🔴 TRENDING\nStrategy paused\nSymbol is SKIPPED")

        # VWAP
        with ind_tabs[3]:
            st.subheader("VWAP — Volume Weighted Average Price")
            st.markdown("""
VWAP is the **average price weighted by how much volume traded at each price level**.
It's the benchmark that institutional traders (banks, funds) use — they try to buy below VWAP and sell above it.

**Why it matters for FlowTrader:**
- Price below VWAP by > 1% = discount zone = institutional buyers likely to step in
- This adds **+1 signal point**

Think of VWAP as the "fair value" price that big players defend. When price drops well below it, buying pressure tends to push it back up.
            """)
            st.info("VWAP is calculated over the last 20 trading days in FlowTrader (a rolling approximation since we use daily bars, not intraday tick data).")

        # ATR
        with ind_tabs[4]:
            st.subheader("ATR — Average True Range")
            st.markdown("""
ATR measures **how much an asset typically moves per day** in dollar terms.
It's a volatility gauge — high ATR = big daily swings, low ATR = calm market.

**FlowTrader uses ATR to set stop-loss distances automatically:**
            """)
            st.markdown("""
| ATR example | Stop distance | What it means |
|------------|---------------|---------------|
| ATR = \$5  | Stop = \$2.50 below entry | Tight stop — calm stock |
| ATR = \$20 | Stop = \$10 below entry | Wider stop — volatile stock |
| ATR = \$2  | Stop = \$1 below entry | Very tight — stable ETF |
            """)
            st.markdown("""
**Formula:** `Stop Loss = Entry Price − (0.5 × ATR)`

This is intentional — the stop is placed close enough to cut losses fast,
but not so close that normal daily noise triggers it accidentally.

**Take profit** is set at the MA20 (the mean). The bot exits half the position at MA20,
then trails the remainder with a 0.25× ATR stop.
            """)

        # Moving Averages
        with ind_tabs[5]:
            st.subheader("MA20 & MA50 — Moving Averages")
            st.markdown("""
A **moving average** smooths out price noise by averaging the last N closing prices.

- **MA20** = average of last 20 trading days (~1 month) — this is FlowTrader's **take-profit target**
- **MA50** = average of last 50 trading days (~2.5 months) — used for broader trend context

**Why MA20 is the target:**
Mean reversion assumes that after a dip, price will return to its recent average.
The 20-day average is that anchor point. When the bot buys an oversold stock,
it expects price to recover back to MA20 — that's where it takes profit.
            """)
            x2 = np.arange(50)
            np.random.seed(7)
            p2 = 150 + np.cumsum(np.random.randn(50) * 1.2)
            ma20_line = pd.Series(p2).rolling(20, min_periods=1).mean().values
            ma_fig = go.Figure()
            ma_fig.add_trace(go.Scatter(x=x2, y=p2, name="Price", line=dict(color="#fafafa", width=2)))
            ma_fig.add_trace(go.Scatter(x=x2, y=ma20_line, name="MA20 (take-profit target)", line=dict(color="#4cc9f0", width=2, dash="dot")))
            ma_fig.add_annotation(x=49, y=ma20_line[-1], text="← Target", showarrow=False, font=dict(color="#4cc9f0"), xanchor="left")
            ma_fig.update_layout(paper_bgcolor="#0e1117", plot_bgcolor="#0e1117", font_color="#fafafa",
                                 height=240, margin=dict(l=0,r=0,t=8,b=0),
                                 yaxis=dict(gridcolor="#1c1f26"), xaxis=dict(showgrid=False),
                                 legend=dict(orientation="h"))
            st.plotly_chart(ma_fig, use_container_width=True)

    # ── SUB-TAB 3: Asset Classes ──────────────────────────────────────────────
    with learn_tabs[2]:
        st.subheader("What FlowTrader Trades")

        ac1, ac2, ac3, ac4 = st.tabs(["📱 Tech Stocks", "🏛️ ETFs", "🥇 Commodities", "₿ Crypto"])

        with ac1:
            st.markdown("### Technology Stocks")
            st.markdown("High-volume, well-known companies with strong mean reversion patterns. These are the most liquid instruments FlowTrader trades.")
            for sym in ["NVDA", "AAPL", "MSFT", "AMD", "TSLA", "META"]:
                with st.container():
                    st.markdown(f"**`{sym}`** — {SYMBOL_NAMES.get(sym, sym)}")
            st.info("Tech stocks tend to have high ATR (volatility) and strong RSI mean reversion. Good for the strategy but require wider stops.")

        with ac2:
            st.markdown("### Market ETFs — Exchange Traded Funds")
            st.markdown("""
ETFs are baskets of stocks that trade like a single stock. Instead of buying Apple, you buy SPY and you own a tiny piece of *all* S&P 500 companies.

**Why FlowTrader trades ETFs:**
- Lower risk than individual stocks (diversified)
- Very liquid — easy to enter and exit
- Great for reading the overall market mood
            """)
            etf_data = {
                "Symbol": ["SPY", "QQQ", "IWM"],
                "Full Name": ["S&P 500 ETF", "Nasdaq-100 ETF", "Russell 2000 ETF"],
                "Tracks": ["Top 500 US companies", "Top 100 tech companies", "2000 small US companies"],
                "Use in FlowTrader": ["Primary market benchmark", "Tech sector health", "Small-cap risk appetite"],
            }
            st.dataframe(etf_data, use_container_width=True, hide_index=True)

        with ac3:
            st.markdown("### Commodity ETFs")
            st.markdown("""
Commodities are physical goods — gold, silver, oil. FlowTrader trades these via ETFs (you don't receive actual gold bars).

**Why commodities?**
They move independently of stocks. When the stock market crashes, gold often rises.
This gives FlowTrader opportunities even in bear markets.
            """)
            comm_data = {
                "Symbol": ["GLD", "SLV", "USO", "TLT"],
                "Full Name": ["Gold ETF", "Silver ETF", "US Oil Fund", "Treasury Bond ETF"],
                "Tracks": ["Gold spot price", "Silver spot price", "WTI crude oil futures", "Long-dated US bonds"],
                "Key driver": ["Fear / inflation hedge", "Industrial demand + safe haven", "Oil supply & geopolitics", "Interest rate expectations"],
                "Mean reversion?": ["✅ Strong", "✅ Strong", "✅ Good but volatile", "✅ Good"],
            }
            st.dataframe(comm_data, use_container_width=True, hide_index=True)
            st.markdown("""
**TLT** is special — it moves *opposite* to interest rates. When rates rise, TLT falls. When fear spikes and rates drop, TLT rises.
It's also non-correlated to tech stocks, so it can provide trades even when the stock market is trending.

**USO** tracks oil (WTI crude). It's more volatile and affected by geopolitics (Middle East, OPEC decisions).
The ATR filter helps protect against sudden oil price gaps.
            """)

        with ac4:
            st.markdown("### Cryptocurrency")
            st.markdown("""
FlowTrader watches crypto 24/7 (markets never close). Crypto is more volatile than stocks —
larger potential gains *and* larger potential losses.

The same mean reversion strategy applies: buy when it's oversold, exit at the mean.
            """)
            crypto_data = {
                "Symbol": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT", "DOGE/USDT"],
                "Name": ["Bitcoin", "Ethereum", "Solana", "Avalanche", "Chainlink", "Dogecoin"],
                "Role": ["Digital gold — store of value", "Smart contract platform", "Fast transactions blockchain", "Ethereum competitor", "Blockchain oracle network", "Meme coin / sentiment driven"],
                "Volatility": ["Medium", "Medium-High", "High", "High", "High", "Very High"],
            }
            st.dataframe(crypto_data, use_container_width=True, hide_index=True)
            st.warning("Crypto can move 10–30% in a day. FlowTrader's 1% position sizing and ATR-based stop loss are critical safeguards here.")

    # ── SUB-TAB 4: Risk Rules ────────────────────────────────────────────────
    with learn_tabs[3]:
        st.subheader("FlowTrader's Hard Risk Rules")
        st.markdown("These rules are **non-negotiable** — they are coded directly into the bot and cannot be overridden by Claude's analysis.")

        rules = [
            ("1% Position Size Limit", "#40916c",
             "Never risk more than 1% of your total account on a single trade.",
             "On a $10,000 account → max $100 at risk per trade. This ensures no single loss can hurt you badly."),
            ("2% Daily Loss Limit", "#f72585",
             "If the portfolio is down 2% on the day, ALL trading stops for that day.",
             "Prevents a bad day from becoming a catastrophic day. The bot shuts itself off automatically."),
            ("Max 3 Open Positions", "#4cc9f0",
             "Never hold more than 3 positions at the same time.",
             "Keeps risk spread and avoids over-exposure. Forces the bot to be selective."),
            ("Always Set a Stop Loss", "#ffd60a",
             "Every trade has a stop loss placed at order time. No exceptions.",
             "Stop = Entry − (0.5 × ATR). If price falls to the stop, the position is closed automatically."),
            ("No New Trades After 14:55 EST", "#ff9500",
             "The time gate closes 5 minutes before US market close.",
             "Prevents holding equity positions overnight — avoids gap risk from after-hours news."),
            ("No Leverage. Ever.", "#ff5252",
             "FlowTrader only buys what it can afford with cash on hand.",
             "Leverage amplifies losses just as much as gains. Mean reversion strategies are conservative by design."),
            ("Trend Filter: Skip if ADX > 25", "#ffd60a",
             "If a symbol is trending strongly, skip it — even if RSI looks oversold.",
             "In a strong downtrend, RSI can stay low for weeks. The trend filter prevents catching falling knives."),
            ("No Earnings Plays", "#ff9500",
             "Skip any symbol with earnings announced within 48 hours.",
             "Earnings cause extreme volatility that breaks mean reversion. The bot avoids these completely."),
        ]

        for title, color, rule, reason in rules:
            st.markdown(
                f"<div style='background:#1c1f26;border-left:4px solid {color};border-radius:8px;"
                f"padding:14px 18px;margin-bottom:10px'>"
                f"<div style='color:{color};font-weight:700;font-size:1rem'>{title}</div>"
                f"<div style='color:#fafafa;margin-top:4px'>{rule}</div>"
                f"<div style='color:#888;font-size:0.85rem;margin-top:6px'>💡 {reason}</div></div>",
                unsafe_allow_html=True
            )

        st.divider()
        st.subheader("The Math Behind a Trade")
        st.markdown("""
**Example trade on a $10,000 account:**

| Step | Calculation | Result |
|------|------------|--------|
| Account value | — | $10,000 |
| Max risk (1%) | $10,000 × 1% | $100 at risk |
| ATR (daily move) | e.g., NVDA ATR = $8 | $8/day |
| Stop distance | 0.5 × ATR | $4 below entry |
| Shares to buy | $100 ÷ $4 | **25 shares** |
| Entry price | e.g., $120 | $3,000 position |
| Stop loss | $120 − $4 | $116 |
| Take profit | MA20, e.g., $128 | $128 |
| Risk:Reward | $4 risk / $8 reward | **1:2 ratio ✅** |

The bot will only place the trade if R:R ≥ 1.5 (risk 1, target 1.5 or better).
        """)

    # ── SUB-TAB 5: Reading the Dashboard ────────────────────────────────────
    with learn_tabs[4]:
        st.subheader("How to Read the Market Tab")

        st.markdown("### The Grade Column")
        grade_rows = [
            ("A_GRADE", "#40916c", "5–6 signals", "Best possible setup. Rare. Trade at full size."),
            ("B_GRADE", "#4cc9f0", "4 signals",   "Good setup. Trade at normal size."),
            ("C_GRADE", "#ffd60a", "3 signals",   "Minimum threshold. Smaller size, be cautious."),
            ("SKIP",    "#555",    "< 3 signals", "Not enough evidence. Bot passes on this symbol."),
            ("NO_DATA", "#ff5252", "API error",   "Data couldn't be fetched. Not traded."),
        ]
        for grade, color, signals, meaning in grade_rows:
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:16px;background:#1c1f26;"
                f"border-radius:8px;padding:10px 14px;margin-bottom:6px'>"
                f"<span style='color:{color};font-weight:700;font-size:0.9rem;min-width:90px'>{grade}</span>"
                f"<span style='color:#888;min-width:80px'>{signals}</span>"
                f"<span style='color:#fafafa'>{meaning}</span></div>",
                unsafe_allow_html=True
            )

        st.divider()
        st.subheader("What to Watch For")
        w1, w2 = st.columns(2)
        with w1:
            st.markdown("**🟢 Bullish signs:**")
            st.markdown("""
- RSI dropping below 32 on a quality symbol
- Price touching or breaking below lower Bollinger Band
- ADX staying below 20 (calm, ranging market)
- GLD or TLT showing oversold — risk-off environment may reverse
- Sentiment turning positive after a selloff
            """)
        with w2:
            st.markdown("**🔴 Warning signs:**")
            st.markdown("""
- ADX rising above 25 on many symbols simultaneously
- All symbols showing SKIP or TRENDING
- Daily loss limit progress bar turning orange/red
- TLT rising sharply (flight to safety — stocks may keep falling)
- Confidence score in Research tab dropping below 5/10
            """)

        st.divider()
        st.subheader("The Four Tabs Explained")
        tab_info = {
            "🔍 Market": "Live scan of all 13 watchlist symbols. Updated every 60 seconds. Shows which symbols have active signals right now.",
            "💼 Account": "Your Alpaca paper trading account balance, open positions, and daily P&L. Tracks the real (paper) performance.",
            "📓 Journal": "Every trade decision logged with full reasoning from Claude. Even SKIP decisions are recorded.",
            "🧠 Research": "Claude's weekly strategic brief — which sectors look good, what to watch for, and confidence score for the week.",
        }
        for tab_name, explanation in tab_info.items():
            st.info(f"**{tab_name}** — {explanation}")


# ── Continuous auto-refresh ───────────────────────────────────────────────────
st.divider()
footer_left, footer_right = st.columns([4, 1])
footer_left.caption(
    f"FlowTrader v1  ·  {'Paper' if PAPER_MODE else 'Live'} trading  ·  "
    + (f"Auto-refreshes every {REFRESH_SEC} s" if auto_refresh else "Auto-refresh paused — toggle in sidebar to resume")
)
countdown_slot = footer_right.empty()

if "next_refresh" not in st.session_state:
    st.session_state.next_refresh = time.time() + REFRESH_SEC

if auto_refresh:
    remaining = int(st.session_state.next_refresh - time.time())
    if remaining <= 0:
        st.session_state.next_refresh = time.time() + REFRESH_SEC
        st.cache_data.clear()
        st.rerun()
    else:
        countdown_slot.caption(f"Next refresh in {remaining}s")
        time.sleep(1)
        st.rerun()
else:
    countdown_slot.caption("Auto-refresh paused")
