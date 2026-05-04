@echo off
REM FlowTrader Bybit balance pusher — invoked by Windows Task Scheduler.
REM Logs to push_bybit_balance.log next to this script.
cd /d "%~dp0\.."
"C:\Python313\python.exe" scripts\push_bybit_balance.py >> "%~dp0push_bybit_balance.log" 2>&1
