# FlowTrader Dashboard

Live trading dashboard for the FlowTrader mean reversion bot.
Built with Streamlit, powered by Claude (Anthropic) and Alpaca.

## Tabs

| Tab | What it shows |
|-----|---------------|
| 🔍 Market | Watchlist scan — signal scores, RSI, Bollinger Bands, VWAP, ADX, sentiment, headlines |
| 💼 Account | Portfolio value, buying power, open positions (P&L coloured), day P&L chart, risk gauges |
| 📓 Journal | Every bot decision — trade/skip breakdown charts, signal score distribution, entry inspector with Claude's full reasoning |
| 🧠 Research | Weekly Research Analyst memo — confidence score, regime, top opportunities, sector focus, watchlist changes, risk warnings |

Auto-refreshes every 60 seconds with a live countdown.

## Deploy to Streamlit Community Cloud

1. Fork or connect this repo at [share.streamlit.io](https://share.streamlit.io)
2. Set **Main file path** to `dashboard.py`
3. Add your secrets under **App settings → Secrets**:

```toml
ALPACA_API_KEY       = "..."
ALPACA_SECRET_KEY    = "..."
ANTHROPIC_API_KEY    = "..."
ALPHAVANTAGE_API_KEY = "..."
PAPER_TRADING        = "true"
TELEGRAM_BOT_TOKEN   = "..."
TELEGRAM_CHAT_ID     = "..."
```

## Run locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# fill in your keys
streamlit run dashboard.py
```
