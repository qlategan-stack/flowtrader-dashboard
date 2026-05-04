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
WATCHLIST    = CFG.get("watchlist", {}).get("equities", ["SPY", "QQQ"])
CRYPTO_LIST  = CFG.get("watchlist", {}).get("crypto", [])
REFRESH_SEC  = 60
PAPER_MODE   = os.getenv("PAPER_TRADING", "true").lower() == "true"

MEMO_JSON = Path("journal/weekly_research_memo.json")

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
    "BTC/USDT": "Bitcoin / Tether USD",
    "ETH/USDT": "Ethereum / Tether USD",
    "SOL/USDT": "Solana / Tether USD",
    "AVAX/USDT": "Avalanche / Tether USD",
    "LINK/USDT": "Chainlink / Tether USD",
    "DOGE/USDT": "Dogecoin / Tether USD",
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

@st.cache_data(ttl=300)
def load_research_memo() -> dict:
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

    # ── Bot info ──────────────────────────────────────────────────────────────
    st.markdown("**Trading Bot**")
    st.caption("The bot runs automatically via GitHub Actions every 30 min on weekdays. Journal data syncs here automatically.")

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

h1, h2, h3 = st.columns([5, 2, 1])
h1.markdown(f"## 📈 FlowTrader  {mode_badge}", unsafe_allow_html=True)
h2.caption(f"Updated: {now_str}")
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
    tradeable = [s for s in wl if s.get("setup_quality") not in ["SKIP", "NO_DATA"]]
    top       = wl[0] if wl else {}
    trending  = sum(1 for s in wl if s.get("indicators", {}).get("regime") == "TRENDING")
    a_grades  = sum(1 for s in wl if s.get("setup_quality") == "A_GRADE")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Symbols Scanned", len(wl),
              help="Total symbols in your active watchlist being scanned this cycle.")
    c2.metric("Tradeable Setups", len(tradeable),
              help="Symbols with 3+ signals fired AND in a ranging market (ADX < 25). These are candidates for a trade.")
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
            "Grade":     st.column_config.TextColumn("Grade",     help="Setup quality: A_GRADE (5-6 signals) → best | B_GRADE (4) | C_GRADE (3) | SKIP | NO_DATA"),
            "Score":     st.column_config.NumberColumn("Score",   help="Signal score 0–6. Need ≥ 3 to trade. Each indicator that fires adds 1 point (RSI<32 adds 2)."),
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

    # ── Symbol Detail (pill selector) ─────────────────────────────────────────
    st.divider()
    st.subheader("Symbol Detail")

    if wl:
        eq_lookup = {item["symbol"]: item for item in wl}
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
            default=eq_symbols[0],
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

            header = f"**{selected_sym}**" + (f" — {full}" if full else "")
            header += f"   ·   Score {score}/6   ·   {grade}   ·   {ind.get('regime','?')}"
            st.markdown(header)

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

    # ── Crypto watchlist — live market data ──────────────────────────────────
    if CRYPTO_LIST:
        st.divider()
        _b           = _bybit()
        order_mode   = "🟡 TESTNET" if _b.testnet else "🔴 LIVE"
        has_key      = _b._has_private
        st.subheader(f"Crypto Watchlist — Orders: Bybit {order_mode}")

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

            # ── Crypto Symbol Detail (pill selector) ──────────────────────────
            st.divider()
            st.subheader("Crypto Symbol Detail")

            cr_lookup = {item["symbol"]: item for item in crypto_wl}
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
                default=cr_symbols[0] if cr_symbols else None,
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
        portfolio  = float(acct.get("portfolio_value", 0))
        buying_pwr = float(acct.get("buying_power", 0))
        cash       = float(acct.get("cash", 0))
        day_pl     = float(acct.get("day_pl", 0))
        open_pos   = int(acct.get("open_positions", 0))

        # ── Top metrics ───────────────────────────────────────────────────────────
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Portfolio Value",  f"${portfolio:,.2f}")
        c2.metric("Buying Power",     f"${buying_pwr:,.2f}")
        c3.metric("Cash",             f"${cash:,.2f}")
        c4.metric("Day P&L",
                  f"${day_pl:+,.2f}",
                  f"{day_pl/portfolio*100:+.2f}%" if portfolio else "0%",
                  delta_color="normal")
        c5.metric("Invested",
                  f"${portfolio - cash:,.2f}",
                  f"{(portfolio - cash)/portfolio*100:.1f}% of portfolio" if portfolio else "—")

        st.divider()

        # ── Risk gauges ───────────────────────────────────────────────────────────
        risk_col1, risk_col2 = st.columns(2)

        with risk_col1:
            st.subheader("Position Capacity")
            cap_frac = open_pos / 3
            cap_icon = "🔴" if cap_frac >= 1.0 else "🟡" if cap_frac >= 0.67 else "🟢"
            st.progress(cap_frac, text=f"{cap_icon} {open_pos} / 3 positions used")

        with risk_col2:
            st.subheader("Daily Loss Limit")
            max_loss  = portfolio * 0.02
            loss_used = abs(day_pl) if day_pl < 0 else 0
            loss_frac = min(loss_used / max_loss, 1.0) if max_loss else 0
            loss_icon = "🔴" if loss_frac >= 0.8 else "🟡" if loss_frac >= 0.5 else "🟢"
            st.progress(loss_frac,
                        text=f"{loss_icon} ${loss_used:,.2f} of ${max_loss:,.2f} max ({loss_frac:.0%} used)")

        st.divider()

        # ── Open positions table ──────────────────────────────────────────────────
        positions = acct.get("positions", [])
        st.subheader(f"Open Positions ({len(positions)} / 3)")

        if not positions:
            st.info("No open positions.")
        else:
            prows = []
            for p in positions:
                pl    = float(p.get("unrealized_pl", 0))
                plpct = float(p.get("unrealized_plpc", 0)) * 100
                prows.append({
                    "Symbol":        p.get("symbol"),
                    "Qty":           float(p.get("qty", 0)),
                    "Avg Entry":     float(p.get("avg_entry", 0)),
                    "Current Price": float(p.get("current_price", 0)),
                    "Unrealized P&L": pl,
                    "P&L %":         plpct,
                })
            pdf = pd.DataFrame(prows)

            def _pl_colour(v):
                return "color:#00c853;font-weight:bold" if v >= 0 else "color:#ff5252;font-weight:bold"

            st.dataframe(
                pdf.style
                   .map(_pl_colour, subset=["Unrealized P&L", "P&L %"])
                   .format({
                       "Avg Entry":      "${:,.2f}",
                       "Current Price":  "${:,.2f}",
                       "Unrealized P&L": "${:+,.2f}",
                       "P&L %":          "{:+.2f}%",
                       "Qty":            "{:,.4f}",
                   }),
                use_container_width=True,
                hide_index=True,
            )

        # ── Bybit crypto account ──────────────────────────────────────────────────
        st.divider()
        bybit_mode = "Testnet" if _bybit().testnet else "Live"
        st.subheader(f"Bybit Crypto Account ({bybit_mode})")

        bybit_bal = fetch_bybit_balance()
        if "error" in bybit_bal:
            st.info(f"Bybit: {bybit_bal['error']}")
        else:
            bb1, bb2, bb3 = st.columns(3)
            bb1.metric("Total Balance (USDT)", f"${bybit_bal.get('total_usdt', 0):,.2f}")
            bb2.metric("Free to Trade (USDT)", f"${bybit_bal.get('free_usdt', 0):,.2f}")
            bb3.metric("Open Crypto Positions", bybit_bal.get("open_positions", 0))
            holdings = bybit_bal.get("positions", [])
            fetched = bybit_bal.get("fetched_at", "")
            if holdings:
                st.caption("Holdings: " + ", ".join(
                    f"{p['currency']} ({p['amount']:.6f})" for p in holdings
                ))
            if fetched:
                st.caption(f"Last updated by bot: {fetched[:16].replace('T', ' ')} UTC")

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
                ))
                pie_fig.update_layout(
                    paper_bgcolor="#0e1117", font_color="#fafafa",
                    height=200, margin=dict(l=0, r=0, t=4, b=0),
                    showlegend=True, legend=dict(orientation="h"),
                )
                st.plotly_chart(pie_fig, use_container_width=True)

        st.divider()

        # ── Journal table ─────────────────────────────────────────────────────
        st.subheader("All Entries")
        rows = []
        for e in reversed(filtered):
            rows.append({
                "Date":       e.get("date", ""),
                "Time":       e.get("time_est", ""),
                "Action":     e.get("action", ""),
                "Symbol":     e.get("symbol") or "—",
                "Score":      e.get("signal_score", 0),
                "Confidence": e.get("confidence", "—"),
                "Entry $":    e.get("entry_price"),
                "Stop $":     e.get("stop_loss"),
                "Target $":   e.get("take_profit"),
                "R:R":        e.get("risk_reward") or "—",
                "Exec":       e.get("execution_status", "—"),
                "Mode":       "Paper" if e.get("paper_trade", True) else "Live",
            })

        jdf = pd.DataFrame(rows)

        def _format_price(v):
            return f"${v:,.2f}" if v is not None else "—"

        st.dataframe(
            jdf.style
               .map(_action_colour, subset=["Action"])
               .map(_exec_colour,   subset=["Exec"])
               .map(_score_colour,  subset=["Score"])
               .format({
                   "Entry $":  _format_price,
                   "Stop $":   _format_price,
                   "Target $": _format_price,
               }),
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
            "No research memo found yet.\n\n"
            "The Research Analyst runs automatically **every Sunday at 18:00 EST** via GitHub Actions.\n\n"
            "To run it manually: `python main.py research-analyst`"
        )
    else:
        gen_at   = memo.get("generated_at", "Unknown")
        valid_to = memo.get("valid_until", "Unknown")
        expired  = memo.get("expired", False)

        # ── Memo header ───────────────────────────────────────────────────────
        rh1, rh2 = st.columns([3, 1])
        with rh1:
            st.subheader("Weekly Research Memo")
            st.caption(f"Generated: {gen_at[:19].replace('T',' ')} EST   |   Valid until: {valid_to[:10]}")
        with rh2:
            if expired:
                st.warning("⚠️ Memo expired — run fresh analysis")

        # ── Confidence + Regime ───────────────────────────────────────────────
        confidence = memo.get("confidence_score", 0)
        raw_regime = memo.get("market_regime", "UNKNOWN")
        if isinstance(raw_regime, dict):
            regime = raw_regime.get("trend_or_range", str(raw_regime))[:120]
        else:
            regime = str(raw_regime)
        reason     = memo.get("confidence_reason", "")

        conf_col, reg_col, reason_col = st.columns([1, 1, 3])

        conf_color = "#00c853" if confidence >= 7 else "#ffab00" if confidence >= 5 else "#ff5252"
        conf_col.metric("Trading Confidence", f"{confidence}/10")

        reg_color = "#ff5252" if "TREND" in regime.upper() else "#00c853"
        reg_col.metric("Market Regime", regime)

        reason_col.info(f"**Rationale:** {reason}")

        # ── Confidence gauge (horizontal bar) ────────────────────────────────
        st.progress(confidence / 10,
                    text=f"Confidence {confidence}/10  —  {'Strong conditions' if confidence >= 7 else 'Moderate conditions' if confidence >= 5 else 'Caution — reduced activity recommended'}")

        st.divider()

        # ── Top 3 Opportunities ───────────────────────────────────────────────
        opportunities = memo.get("top_opportunities", [])
        st.subheader(f"Top {len(opportunities)} Opportunities This Week")

        if opportunities:
            opp_cols = st.columns(min(len(opportunities), 3))
            for i, opp in enumerate(opportunities[:3]):
                with opp_cols[i]:
                    if isinstance(opp, dict):
                        sym    = opp.get("symbol", "?")
                        why    = opp.get("reason", opp.get("why", ""))
                        score  = opp.get("signal_strength", opp.get("score", ""))
                        sizing = opp.get("position_size_note", "")
                        st.markdown(f"### {sym}")
                        st.caption(why[:200] if why else "—")
                        if score:
                            st.caption(f"Signal strength: {score}")
                        if sizing:
                            st.caption(f"Sizing note: {sizing}")
                    else:
                        st.markdown(f"- {str(opp)[:200]}")
        else:
            st.info("No specific opportunities identified this week.")

        st.divider()

        # ── Sector Performance ────────────────────────────────────────────────
        sector_focus = memo.get("sector_focus", {})
        st.subheader("Sector Focus")

        sec_col1, sec_col2 = st.columns(2)
        with sec_col1:
            favour = sector_focus.get("favour", sector_focus.get("best", []))
            st.markdown("**Sectors to Favour**")
            if isinstance(favour, list):
                for s in favour:
                    st.success(f"✅ {s}" if isinstance(s, str) else f"✅ {s.get('sector','?')} — {s.get('reason','')[:80]}")
            elif favour:
                st.success(str(favour))

        with sec_col2:
            avoid_sec = sector_focus.get("avoid", sector_focus.get("worst", []))
            st.markdown("**Sectors to Avoid / Underweight**")
            if isinstance(avoid_sec, list):
                for s in avoid_sec:
                    st.error(f"⛔ {s}" if isinstance(s, str) else f"⛔ {s.get('sector','?')} — {s.get('reason','')[:80]}")
            elif avoid_sec:
                st.error(str(avoid_sec))

        st.divider()

        # ── Watchlist Changes ─────────────────────────────────────────────────
        wl_changes = memo.get("watchlist_changes", {})
        st.subheader("Watchlist Recommendations")

        wc1, wc2, wc3 = st.columns(3)
        with wc1:
            adds = wl_changes.get("add", [])
            st.markdown("**Add to Watchlist**")
            if adds:
                for sym in adds:
                    st.success(f"➕ {sym}")
            else:
                st.caption("No additions recommended")

        with wc2:
            removes = wl_changes.get("remove", [])
            st.markdown("**Remove from Watchlist**")
            if removes:
                for sym in removes:
                    st.warning(f"➖ {sym}")
            else:
                st.caption("No removals recommended")

        with wc3:
            avoid_syms = wl_changes.get("avoid_earnings", wl_changes.get("avoid", []))
            st.markdown("**Avoid (Earnings Risk)**")
            if avoid_syms:
                for sym in avoid_syms:
                    st.error(f"⚠️ {sym}")
            else:
                st.caption("No earnings conflicts this week")

        st.divider()

        # ── Risk Warnings ─────────────────────────────────────────────────────
        risk_warnings = memo.get("risk_warnings", [])
        st.subheader("Risk Warnings")

        if risk_warnings:
            for warn in risk_warnings:
                if isinstance(warn, dict):
                    severity = str(warn.get("severity", "")).upper()
                    text     = warn.get("warning", warn.get("description", str(warn)))
                    if severity in ["HIGH", "CRITICAL"]:
                        st.error(f"🔴 {text}")
                    elif severity == "MEDIUM":
                        st.warning(f"🟡 {text}")
                    else:
                        st.info(f"🔵 {text}")
                else:
                    st.warning(f"⚠️ {str(warn)}")
        else:
            st.success("No significant risk warnings for this week.")

        st.divider()

        # ── Raw analysis (collapsible) ────────────────────────────────────────
        with st.expander("Full Raw Analysis from Claude"):
            raw = memo.get("raw_analysis", "")
            if raw:
                st.markdown(raw)
            else:
                st.info("No raw analysis text available.")

        # ── Full JSON (debug) ─────────────────────────────────────────────────
        with st.expander("Memo JSON (debug)"):
            st.json({k: v for k, v in memo.items() if k != "raw_analysis"})


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
