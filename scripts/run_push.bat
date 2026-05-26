@echo off
REM FlowTrader crypto balance pusher — invoked by Windows Task Scheduler.
REM
REM NAMING NOTE (L-1 audit 2026-05-26): the "bybit" in the script and log
REM file names is legacy. The bot now uses BinanceFetcher when BINANCE_API_KEY
REM is set (current state), falling back to Bybit otherwise. The internal
REM "exchange" field of the JSON reflects the actual venue; file names are
REM kept as-is to avoid breaking Task Scheduler + Streamlit Cloud refs.
cd /d "%~dp0\.."
python scripts\push_bybit_balance.py >> "%~dp0push_bybit_balance.log" 2>&1
