"""
data/crypto_fetcher.py
CCXT + Bybit integration for crypto market data and order execution.
Produces the same indicator/snapshot structure as MarketDataFetcher
so Claude's decision agent works identically for equities and crypto.

Public market data (OHLCV, tickers) uses Bybit's public REST API directly
as the primary path — no API key, no ccxt market loading required.
ccxt is used only for private operations (balance, orders).
"""

import os
import logging
import requests
from pathlib import Path
from typing import Optional
import pandas as pd
import pytz
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
logger = logging.getLogger(__name__)

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False
    logger.warning("ccxt not installed — run: pip install ccxt")

try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    logger.warning("ta not installed — run: pip install ta")

BYBIT_REST_BASE    = "https://api.bybit.com"
BINANCE_REST_BASE  = "https://api.binance.com"
COINBASE_REST_BASE = "https://api.exchange.coinbase.com"
KRAKEN_REST_BASE   = "https://api.kraken.com"


class BybitFetcher:
    """
    Fetches crypto market data from Bybit and calculates the same technical
    indicators as MarketDataFetcher so Claude's decision agent works
    identically for equities and crypto.

    Public data (OHLCV, tickers): uses Bybit REST API directly — no API key,
    no ccxt market-loading required. Works on any hosting environment.
    Private data (balance, orders): BYBIT_API_KEY + BYBIT_SECRET_KEY required.
    """

    def __init__(self):
        self.api_key    = os.getenv("BYBIT_API_KEY", "")
        self.api_secret = os.getenv("BYBIT_SECRET_KEY", "")
        self.testnet    = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
        self.exchange        = None   # ccxt — public market data (optional)
        self.exchange_priv   = None   # ccxt testnet/live — orders & balance
        self._connected      = False  # ccxt public connection succeeded
        self._connect_error  = None   # last ccxt connection error (for UI display)
        self._has_private    = bool(self.api_key and self.api_secret)
        self._active_source  = None   # name of last data source that returned data

        if CCXT_AVAILABLE:
            self._connect()

    def _connect(self):
        # Public exchange — live Bybit, no auth, full symbol set + real prices
        try:
            self.exchange = ccxt.bybit({
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                },
                "enableRateLimit": True,
            })
            self.exchange.load_markets()
            self._connected = True
            logger.info("Bybit public exchange connected (live market data)")
        except Exception as e:
            self._connected = False
            self._connect_error = str(e)
            logger.error(f"Bybit public connection failed (market data will use REST): {e}")

        # Private exchange — testnet/live, for orders & balance only.
        # load_markets() is best-effort — balance/orders still work without it.
        try:
            self.exchange_priv = ccxt.bybit({
                "apiKey":  self.api_key  or None,
                "secret":  self.api_secret or None,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                },
                "enableRateLimit": True,
            })
            if self.testnet:
                self.exchange_priv.set_sandbox_mode(True)  # → api-testnet.bybit.com
            mode = "TESTNET orders" if self.testnet else "LIVE orders"
            key_status = "API key loaded" if self._has_private else "no key — balance/orders unavailable"
            logger.info(f"Bybit private exchange ready ({mode}) — {key_status}")
        except Exception as e:
            self.exchange_priv = None
            self._has_private = False
            self._connect_error = str(e)
            logger.warning(f"Bybit private exchange init failed: {e}")

        if self.exchange_priv is not None and self._has_private:
            try:
                self.exchange_priv.load_markets()
            except Exception as e:
                logger.warning(f"Bybit private load_markets failed (continuing — balance still works): {e}")

    # ── DATA FETCHING ─────────────────────────────────────────────────────────

    @staticmethod
    def _bybit_symbol(symbol: str) -> str:
        """Convert 'BTC/USDT' → 'BTCUSDT' for Bybit/Binance REST."""
        return symbol.replace("/", "")

    @staticmethod
    def _coinbase_symbol(symbol: str) -> str:
        """Convert 'BTC/USDT' → 'BTC-USD' for Coinbase Exchange API."""
        return f"{symbol.split('/')[0]}-USD"

    @staticmethod
    def _kraken_symbol(symbol: str) -> str:
        """Convert 'BTC/USDT' → 'XBTUSDT' for Kraken (BTC is XBT on Kraken)."""
        base = symbol.split("/")[0]
        if base == "BTC":
            base = "XBT"
        quote = symbol.split("/")[1] if "/" in symbol else "USD"
        return f"{base}{quote}"

    def get_ohlcv(self, symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
        """
        Fetch daily OHLCV candles.
        Priority: Coinbase → Kraken → Binance → Bybit → ccxt.
        Coinbase is first because it works reliably from US-hosted cloud
        infrastructure (Streamlit Community Cloud runs in US-East AWS).
        """
        for source, fn in (
            ("coinbase", self._get_ohlcv_coinbase),
            ("kraken",   self._get_ohlcv_kraken),
            ("binance",  self._get_ohlcv_binance),
            ("bybit",    self._get_ohlcv_bybit),
        ):
            df = fn(symbol, days)
            if df is not None and len(df) >= 20:
                self._active_source = source
                return df

        if self._connected:
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, "1d", limit=min(days + 5, 200))
                df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                self._active_source = "ccxt"
                return df.tail(days).reset_index(drop=True)
            except Exception as e:
                logger.error(f"ccxt OHLCV fallback failed for {symbol}: {e}")
        return None

    def _get_ohlcv_coinbase(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """
        Coinbase Exchange — GET /products/{id}/candles?granularity=86400.
        No auth, works from US cloud. Returns up to 300 candles.
        Format: [[time, low, high, open, close, volume], ...] (newest first).
        """
        try:
            r = requests.get(
                f"{COINBASE_REST_BASE}/products/{self._coinbase_symbol(symbol)}/candles",
                params={"granularity": 86400},  # 1-day candles
                timeout=10,
                headers={"User-Agent": "FlowTrader/1.0"},
            )
            if r.status_code != 200:
                logger.warning(f"Coinbase OHLCV {symbol} status {r.status_code}: {r.text[:120]}")
                return None
            rows = r.json()
            if not isinstance(rows, list) or not rows:
                return None
            rows = sorted(rows, key=lambda x: x[0])  # ascending by time
            df = pd.DataFrame(rows, columns=["timestamp", "low", "high", "open", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df[["timestamp", "open", "high", "low", "close", "volume"]].tail(days).reset_index(drop=True)
        except Exception as e:
            logger.error(f"Coinbase OHLCV failed for {symbol}: {e}")
            return None

    def _get_ohlcv_kraken(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """
        Kraken — GET /0/public/OHLC?pair=...&interval=1440 (daily).
        No auth, works globally. Returns up to 720 candles.
        """
        try:
            r = requests.get(
                f"{KRAKEN_REST_BASE}/0/public/OHLC",
                params={"pair": self._kraken_symbol(symbol), "interval": 1440},
                timeout=10,
            )
            data = r.json()
            if data.get("error"):
                return None
            result = data.get("result", {})
            # Kraken keys responses by its own pair name (e.g., XXBTZUSD), not what we sent
            pair_key = next((k for k in result.keys() if k != "last"), None)
            if not pair_key:
                return None
            rows = result[pair_key]
            if not rows:
                return None
            # Format: [time, open, high, low, close, vwap, volume, count]
            df = pd.DataFrame(rows, columns=[
                "timestamp", "open", "high", "low", "close", "vwap", "volume", "count",
            ])
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df[["timestamp", "open", "high", "low", "close", "volume"]].tail(days).reset_index(drop=True)
        except Exception as e:
            logger.error(f"Kraken OHLCV failed for {symbol}: {e}")
            return None

    def _get_ohlcv_binance(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """Binance REST — GET /api/v3/klines. No auth required. Works from all cloud IPs."""
        try:
            r = requests.get(
                f"{BINANCE_REST_BASE}/api/v3/klines",
                params={
                    "symbol":   self._bybit_symbol(symbol),  # BTCUSDT format matches Binance
                    "interval": "1d",
                    "limit":    min(days + 5, 200),
                },
                timeout=10,
            )
            data = r.json()
            if isinstance(data, dict) and data.get("code"):
                logger.warning(f"Binance OHLCV error for {symbol}: {data.get('msg')}")
                return None
            # Binance kline: [openTime, open, high, low, close, volume, closeTime, ...]
            df = pd.DataFrame(data, columns=[
                "timestamp", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ])
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df[["timestamp", "open", "high", "low", "close", "volume"]].tail(days).reset_index(drop=True)
        except Exception as e:
            logger.error(f"Binance OHLCV failed for {symbol}: {e}")
            return None

    def _get_ohlcv_bybit(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """Bybit v5 REST — GET /v5/market/kline. No auth required."""
        try:
            r = requests.get(
                f"{BYBIT_REST_BASE}/v5/market/kline",
                params={
                    "category": "spot",
                    "symbol":   self._bybit_symbol(symbol),
                    "interval": "D",
                    "limit":    min(days + 5, 200),
                },
                timeout=10,
            )
            data = r.json()
            if data.get("retCode") != 0:
                logger.warning(f"Bybit REST OHLCV error for {symbol}: {data.get('retMsg')}")
                return None
            items = list(reversed(data["result"]["list"]))  # Bybit returns newest-first
            if not items:
                return None
            df = pd.DataFrame(
                items,
                columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df.tail(days).reset_index(drop=True)
        except Exception as e:
            logger.error(f"Bybit REST OHLCV failed for {symbol}: {e}")
            return None

    def get_ticker(self, symbol: str) -> dict:
        """
        Fetch live ticker — price, 24h change, volume.
        Priority: Coinbase → Kraken → Binance → Bybit → ccxt.
        """
        for fn in (
            self._get_ticker_coinbase,
            self._get_ticker_kraken,
            self._get_ticker_binance,
            self._get_ticker_bybit,
        ):
            ticker = fn(symbol)
            if ticker:
                return ticker
        if self._connected:
            try:
                t = self.exchange.fetch_ticker(symbol)
                return {
                    "price":             t.get("last", 0),
                    "change_pct_24h":    round(t.get("percentage", 0) or 0, 2),
                    "volume_24h_usdt":   round(t.get("quoteVolume", 0) or 0, 2),
                    "high_24h":          t.get("high", 0),
                    "low_24h":           t.get("low", 0),
                    "bid":               t.get("bid", 0),
                    "ask":               t.get("ask", 0),
                }
            except Exception as e:
                logger.error(f"ccxt ticker fallback failed for {symbol}: {e}")
        return {}

    def _get_ticker_coinbase(self, symbol: str) -> dict:
        """Coinbase Exchange — combine /ticker (live price) + /stats (24h)."""
        try:
            product = self._coinbase_symbol(symbol)
            headers = {"User-Agent": "FlowTrader/1.0"}
            t = requests.get(f"{COINBASE_REST_BASE}/products/{product}/ticker",
                             timeout=10, headers=headers).json()
            s = requests.get(f"{COINBASE_REST_BASE}/products/{product}/stats",
                             timeout=10, headers=headers).json()
            price    = float(t.get("price", 0) or 0)
            open_24h = float(s.get("open", 0) or 0)
            change_pct = ((price - open_24h) / open_24h * 100) if open_24h > 0 else 0.0
            volume   = float(s.get("volume", 0) or 0)  # base-asset volume
            return {
                "price":             price,
                "change_pct_24h":    round(change_pct, 2),
                "volume_24h_usdt":   round(volume * price, 2),  # approximate quote volume
                "high_24h":          float(s.get("high", 0) or 0),
                "low_24h":           float(s.get("low", 0) or 0),
                "bid":               float(t.get("bid", 0) or 0),
                "ask":               float(t.get("ask", 0) or 0),
            }
        except Exception as e:
            logger.error(f"Coinbase ticker failed for {symbol}: {e}")
            return {}

    def _get_ticker_kraken(self, symbol: str) -> dict:
        """Kraken — GET /0/public/Ticker?pair=..."""
        try:
            r = requests.get(
                f"{KRAKEN_REST_BASE}/0/public/Ticker",
                params={"pair": self._kraken_symbol(symbol)},
                timeout=10,
            )
            data = r.json()
            if data.get("error"):
                return {}
            result = data.get("result", {})
            pair_key = next(iter(result.keys()), None)
            if not pair_key:
                return {}
            t = result[pair_key]
            # Kraken format: c=last trade [price, lot vol], v=volume [today, 24h],
            # h=high [today, 24h], l=low [today, 24h], o=opening price, b=bid, a=ask
            price    = float(t["c"][0])
            open_24h = float(t.get("o", 0) or 0)
            change_pct = ((price - open_24h) / open_24h * 100) if open_24h > 0 else 0.0
            volume_24h = float(t["v"][1])  # 24h base volume
            return {
                "price":             price,
                "change_pct_24h":    round(change_pct, 2),
                "volume_24h_usdt":   round(volume_24h * price, 2),
                "high_24h":          float(t["h"][1]),
                "low_24h":           float(t["l"][1]),
                "bid":               float(t["b"][0]),
                "ask":               float(t["a"][0]),
            }
        except Exception as e:
            logger.error(f"Kraken ticker failed for {symbol}: {e}")
            return {}

    def _get_ticker_binance(self, symbol: str) -> dict:
        """Binance REST — GET /api/v3/ticker/24hr. No auth required."""
        try:
            r = requests.get(
                f"{BINANCE_REST_BASE}/api/v3/ticker/24hr",
                params={"symbol": self._bybit_symbol(symbol)},
                timeout=10,
            )
            data = r.json()
            if isinstance(data, dict) and data.get("code"):
                return {}
            return {
                "price":             float(data.get("lastPrice", 0) or 0),
                "change_pct_24h":    round(float(data.get("priceChangePercent", 0) or 0), 2),
                "volume_24h_usdt":   round(float(data.get("quoteVolume", 0) or 0), 2),
                "high_24h":          float(data.get("highPrice", 0) or 0),
                "low_24h":           float(data.get("lowPrice", 0) or 0),
                "bid":               float(data.get("bidPrice", 0) or 0),
                "ask":               float(data.get("askPrice", 0) or 0),
            }
        except Exception as e:
            logger.error(f"Binance ticker failed for {symbol}: {e}")
            return {}

    def _get_ticker_bybit(self, symbol: str) -> dict:
        """Bybit v5 REST — GET /v5/market/tickers. No auth required."""
        try:
            r = requests.get(
                f"{BYBIT_REST_BASE}/v5/market/tickers",
                params={"category": "spot", "symbol": self._bybit_symbol(symbol)},
                timeout=10,
            )
            data = r.json()
            if data.get("retCode") != 0:
                return {}
            items = data["result"].get("list", [])
            if not items:
                return {}
            item = items[0]
            return {
                "price":             float(item.get("lastPrice", 0) or 0),
                "change_pct_24h":    round(float(item.get("price24hPcnt", 0) or 0) * 100, 2),
                "volume_24h_usdt":   round(float(item.get("turnover24h", 0) or 0), 2),
                "high_24h":          float(item.get("highPrice24h", 0) or 0),
                "low_24h":           float(item.get("lowPrice24h", 0) or 0),
                "bid":               float(item.get("bid1Price", 0) or 0),
                "ask":               float(item.get("ask1Price", 0) or 0),
            }
        except Exception as e:
            logger.error(f"Bybit REST ticker failed for {symbol}: {e}")
            return {}

    # ── INDICATORS ────────────────────────────────────────────────────────────

    def calculate_indicators(self, df: Optional[pd.DataFrame]) -> dict:
        """
        Calculate RSI, Bollinger Bands, ADX, VWAP, ATR, and signal score.
        Identical logic to MarketDataFetcher.calculate_indicators.
        """
        if df is None or len(df) < 20:
            return {"error": "Insufficient data"}
        if not TA_AVAILABLE:
            return {"error": "ta library not installed"}

        try:
            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]

            rsi_val   = float(ta.momentum.RSIIndicator(close=close, window=14).rsi().iloc[-1])
            bb        = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
            bb_upper  = float(bb.bollinger_hband().iloc[-1])
            bb_middle = float(bb.bollinger_mavg().iloc[-1])
            bb_lower  = float(bb.bollinger_lband().iloc[-1])
            bb_pct    = float(bb.bollinger_pband().iloc[-1])
            atr_val   = float(ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range().iloc[-1])
            adx_val   = float(ta.trend.ADXIndicator(high=high, low=low, close=close, window=14).adx().iloc[-1])
            ma20      = float(close.rolling(20).mean().iloc[-1])
            ma50      = float(close.rolling(50).mean().iloc[-1]) if len(df) >= 50 else ma20
            vwap      = float(((((high + low + close) / 3) * volume).rolling(20).sum() / volume.rolling(20).sum()).iloc[-1])
            price     = float(close.iloc[-1])

            signals, score = [], 0
            if rsi_val < 32:
                signals.append("RSI<32 (strong oversold)"); score += 2
            elif rsi_val < 40:
                signals.append("RSI<40 (mild oversold)"); score += 1
            if price < bb_lower:
                signals.append("BelowLowerBB"); score += 1
            if vwap > 0 and price < vwap * 0.99:
                signals.append("BelowVWAP>1%"); score += 1
            if adx_val < 20:
                signals.append("ADX<20 (ranging market)"); score += 1

            trending = adx_val > 25
            return {
                "current_price":          price,
                "rsi":                    round(rsi_val, 2),
                "bollinger": {
                    "upper":  round(bb_upper, 2),
                    "middle": round(bb_middle, 2),
                    "lower":  round(bb_lower, 2),
                    "pct_b":  round(bb_pct, 3),
                },
                "vwap":                   round(vwap, 2),
                "atr":                    round(atr_val, 2),
                "adx":                    round(adx_val, 2),
                "ma20":                   round(ma20, 2),
                "ma50":                   round(ma50, 2),
                "stop_loss_price":        round(price - 0.5 * atr_val, 2),
                "take_profit_price":      round(ma20, 2),
                "signal_score":           score,
                "signals_fired":          signals,
                "regime":                 "TRENDING" if trending else "RANGING",
                "mean_reversion_eligible": not trending and score >= 3,
            }
        except Exception as e:
            logger.error(f"Indicator calculation error: {e}")
            return {"error": str(e)}

    # ── ACCOUNT ───────────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """
        Fetch wallet balances across Unified (trading) and Funding accounts.
        Bybit testnet faucet drops USDT into Funding by default — UTA only
        sees coins moved into Unified, so we aggregate both.
        Non-USDT coins are returned as positions, enriched with the current
        spot price so the dashboard can show USD-equivalent value.
        """
        if self.exchange_priv is None:
            why = self._connect_error or "private exchange not initialized"
            return {"error": f"Bybit not connected: {why}"}
        if not self._has_private:
            return {"error": f"No API key (testnet={self.testnet}) — add BYBIT_API_KEY/BYBIT_SECRET_KEY to Streamlit secrets"}
        try:
            unified = self._fetch_unified_balance()
            funding = self._fetch_funding_balance()

            by_coin: dict[str, dict[str, float]] = {}
            for src, label in ((unified, "unified"), (funding, "funding")):
                for coin, qty in src.items():
                    if qty <= 1e-9:
                        continue
                    rec = by_coin.setdefault(coin, {"unified": 0.0, "funding": 0.0})
                    rec[label] += qty

            usdt_unified = by_coin.pop("USDT", {"unified": 0.0, "funding": 0.0}) if "USDT" in by_coin else {"unified": 0.0, "funding": 0.0}
            usdt_total   = round(usdt_unified["unified"] + usdt_unified["funding"], 2)
            usdt_free    = round(unified.get("USDT", 0.0), 2)  # Unified wallet free balance is what's available to trade

            positions = []
            for coin, rec in sorted(by_coin.items()):
                amount = rec["unified"] + rec["funding"]
                price  = self._coin_usd_price(coin)
                positions.append({
                    "currency":  coin,
                    "amount":    round(amount, 8),
                    "unified":   round(rec["unified"], 8),
                    "funding":   round(rec["funding"], 8),
                    "price_usd": round(price, 2) if price else None,
                    "value_usd": round(amount * price, 2) if price else None,
                })

            position_value = sum((p["value_usd"] or 0) for p in positions)
            account_value  = round(usdt_total + position_value, 2)

            return {
                "account_value":  account_value,           # USDT + USD-valued positions
                "total_usdt":     usdt_total,              # USDT across both wallets
                "free_usdt":      usdt_free,               # USDT in Unified, available to trade
                "funding_usdt":   round(usdt_unified["funding"], 2),
                "position_value": round(position_value, 2),
                "open_positions": len(positions),
                "positions":      positions,
                "exchange":       "bybit",
                "testnet":        self.testnet,
            }
        except Exception as e:
            msg = str(e)
            if "403" in msg or "Forbidden" in msg or "block access" in msg.lower():
                msg = "Bybit testnet endpoint is geo-blocked from this server."
            logger.error(f"Bybit balance error: {e}")
            return {"error": msg}

    def _fetch_unified_balance(self) -> dict:
        """Return {coin: walletBalance} for the Unified Trading Account."""
        try:
            raw = self.exchange_priv.privateGetV5AccountWalletBalance({"accountType": "UNIFIED"})
            if int(raw.get("retCode", -1)) != 0:
                logger.warning(f"Unified balance error: {raw.get('retMsg')}")
                return {}
            coins = (raw.get("result", {}).get("list") or [{}])[0].get("coin", [])
            out = {}
            for coin in coins:
                sym = coin.get("coin", "")
                # availableToWithdraw is what's actually free; walletBalance can include locked margin.
                # For UTA we want the wallet balance for display, but free for USDT trading capacity.
                qty = float(coin.get("walletBalance", 0) or 0)
                if sym == "USDT":
                    qty = float(coin.get("availableToWithdraw") or coin.get("walletBalance") or 0)
                if qty > 0:
                    out[sym] = qty
            return out
        except Exception as e:
            logger.warning(f"Unified balance fetch failed: {e}")
            return {}

    def _fetch_funding_balance(self) -> dict:
        """Return {coin: walletBalance} for the Funding (FUND) account."""
        try:
            raw = self.exchange_priv.privateGetV5AssetTransferQueryAccountCoinsBalance({"accountType": "FUND"})
            if int(raw.get("retCode", -1)) != 0:
                logger.warning(f"Funding balance error: {raw.get('retMsg')}")
                return {}
            balances = raw.get("result", {}).get("balance", []) or []
            out = {}
            for b in balances:
                sym = b.get("coin", "")
                qty = float(b.get("walletBalance", 0) or 0)
                if qty > 0:
                    out[sym] = qty
            return out
        except Exception as e:
            logger.warning(f"Funding balance fetch failed: {e}")
            return {}

    def _coin_usd_price(self, coin: str) -> float:
        """Spot USD price for a coin via existing ticker fallback chain. 0 if unavailable."""
        if coin in ("USDT", "USDC", "USD", "DAI"):
            return 1.0
        try:
            t = self.get_ticker(f"{coin}/USDT")
            return float(t.get("price", 0) or 0)
        except Exception:
            return 0.0

    # ── ORDER PLACEMENT ───────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        usdt_amount: float,
        current_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> dict:
        """
        Place a spot market order + stop-loss trigger on Bybit.
        Requires BYBIT_API_KEY and BYBIT_SECRET_KEY.
        """
        if self.exchange_priv is None:
            return {"status": "ERROR", "reason": "Bybit not connected"}
        if not self._has_private:
            return {"status": "ERROR", "reason": "No API key — add BYBIT_API_KEY to .env"}

        try:
            base_amount = usdt_amount / current_price

            if side.upper() == "BUY":
                order = self.exchange_priv.create_market_buy_order(symbol, base_amount)
                fill_price = float(order.get("average") or order.get("price") or current_price)

                # Attach stop-loss as a separate trigger order
                try:
                    self.exchange_priv.create_order(
                        symbol, "limit", "sell", base_amount, stop_loss_price,
                        params={"triggerPrice": str(stop_loss_price), "orderType": "Limit"}
                    )
                except Exception as sl_err:
                    logger.warning(f"Stop-loss order failed (main order still placed): {sl_err}")

                return {
                    "status":      "SUBMITTED",
                    "exchange":    "bybit",
                    "testnet":     self.testnet,
                    "symbol":      symbol,
                    "side":        "BUY",
                    "base_amount": round(base_amount, 6),
                    "usdt_spent":  round(usdt_amount, 2),
                    "fill_price":  round(fill_price, 6),
                    "stop_loss":   stop_loss_price,
                    "take_profit": take_profit_price,
                    "order_id":    order.get("id"),
                }

            else:  # SELL
                order = self.exchange_priv.create_market_sell_order(symbol, base_amount)
                fill_price = float(order.get("average") or order.get("price") or current_price)
                return {
                    "status":         "SUBMITTED",
                    "exchange":       "bybit",
                    "testnet":        self.testnet,
                    "symbol":         symbol,
                    "side":           "SELL",
                    "base_amount":    round(base_amount, 6),
                    "usdt_received":  round(base_amount * fill_price, 2),
                    "fill_price":     round(fill_price, 6),
                    "order_id":       order.get("id"),
                }

        except Exception as e:
            logger.error(f"Bybit order error: {e}")
            return {"status": "ERROR", "reason": str(e), "symbol": symbol}

    # ── SNAPSHOT ─────────────────────────────────────────────────────────────

    def _rate_setup(self, indicators: dict) -> str:
        if "error" in indicators:
            return "NO_DATA"
        score    = indicators.get("signal_score", 0)
        eligible = indicators.get("mean_reversion_eligible", False)
        if not eligible:
            return "SKIP"
        if score >= 5:  return "A_GRADE"
        if score >= 4:  return "B_GRADE"
        if score >= 3:  return "C_GRADE"
        return "SKIP"

    def build_crypto_snapshot(self, symbols: list) -> list:
        """
        Build a watchlist snapshot for all crypto symbols.
        Returns a list in the same format as MarketDataFetcher.build_market_snapshot
        so the dashboard can render both with the same code.
        """
        results = []
        for symbol in symbols:
            logger.info(f"Fetching Bybit data for {symbol}...")
            df         = self.get_ohlcv(symbol, days=60)
            indicators = self.calculate_indicators(df)
            ticker     = self.get_ticker(symbol)

            # Use live ticker price if indicator calc failed
            if "current_price" not in indicators and ticker.get("price"):
                indicators["current_price"] = ticker["price"]

            results.append({
                "symbol":        symbol,
                "indicators":    indicators,
                "ticker":        ticker,
                "news_sentiment": {"sentiment": "neutral", "score": 0.0, "source": "none"},
                "recent_headlines": [],
                "setup_quality": self._rate_setup(indicators),
                "exchange":      "bybit",
            })

        results.sort(key=lambda x: x["indicators"].get("signal_score", 0), reverse=True)
        return results


class BinanceFetcher:
    """
    Binance Spot trading via ccxt — authenticated operations only.
    Public market data (OHLCV, tickers) is handled by BybitFetcher's
    multi-source fallback chain; no duplication needed here.

    Set BINANCE_TESTNET=true to target testnet.binance.vision (default).
    Set BINANCE_TESTNET=false for the live exchange.
    """

    def __init__(self):
        self.api_key      = os.getenv("BINANCE_API_KEY", "")
        self.api_secret   = os.getenv("BINANCE_SECRET_KEY", "")
        self.testnet      = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
        self.exchange     = None
        self._has_private = bool(self.api_key and self.api_secret)
        self._markets_loaded = False

        if CCXT_AVAILABLE and self._has_private:
            self._connect()

    def _connect(self):
        try:
            self.exchange = ccxt.binance({
                "apiKey":  self.api_key,
                "secret":  self.api_secret,
                "options": {"defaultType": "spot"},
                "enableRateLimit": True,
            })
            if self.testnet:
                self.exchange.set_sandbox_mode(True)
                # testnet.binance.vision uses a CA not in Python's certifi bundle;
                # ccxt passes verify=exchange.verify to requests.
                self.exchange.verify = False
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            # load_markets() is deferred — it takes ~2 min on testnet and is only
            # needed for precision methods used in order placement.
            self._markets_loaded = False
            mode = "TESTNET" if self.testnet else "LIVE"
            logger.info(f"Binance connected ({mode}) — API key loaded")
        except Exception as e:
            self.exchange = None
            self._has_private = False
            logger.error(f"Binance connection failed: {e}")

    def _ensure_markets(self):
        """Load markets once, lazily — only needed before order placement."""
        if not self._markets_loaded and self.exchange:
            logger.info("Binance: loading markets (one-time, needed for order precision)…")
            self.exchange.load_markets()
            self._markets_loaded = True

    # ── ACCOUNT ───────────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """Fetch Binance spot USDT balance and open coin positions.

        Ticker lookups are batched in one fetch_tickers() call (capped at 20
        symbols) to avoid the per-coin request avalanche that testnet accounts
        accumulate as dust after many test trades.
        """
        if not self.exchange or not self._has_private:
            return {"error": "Binance not connected — add BINANCE_API_KEY to .env"}
        try:
            raw = self.exchange.fetch_balance()
            usdt_free  = float((raw.get("USDT") or {}).get("free",  0) or 0)
            usdt_total = float((raw.get("USDT") or {}).get("total", 0) or 0)

            # Fiat / stable coins that Binance doesn't pair against USDT (so
            # fetch_tickers raises "no market symbol X/USDT"). TRY in particular
            # is a residual fiat balance on the testnet account.
            _FIAT_SKIP = {"TRY", "EUR", "GBP", "AUD", "BRL", "RUB", "USD",
                          "BUSD", "USDC", "TUSD", "FDUSD", "DAI"}

            coin_amounts: dict = {}
            for coin, data in (raw.get("total") or {}).items():
                if coin == "USDT" or coin in _FIAT_SKIP:
                    continue
                amount = float(data or 0)
                # Filter dust, fake testnet tokens (non-ASCII names, digit-only names)
                if amount > 1e-4 and coin.isascii() and coin.isalnum() and not coin.isdigit():
                    coin_amounts[coin] = amount

            prices: dict = {}
            if coin_amounts:
                symbols = [f"{c}/USDT" for c in list(coin_amounts)[:20]]
                try:
                    tickers = self.exchange.fetch_tickers(symbols)
                    for sym, ticker in tickers.items():
                        coin = sym.split("/")[0]
                        prices[coin] = float(ticker.get("last", 0) or 0)
                except Exception as te:
                    logger.warning(f"Binance batch ticker fetch failed: {te}")

            positions = []
            for coin, amount in list(coin_amounts.items())[:20]:
                price = prices.get(coin)
                value = round(amount * price, 2) if price else None
                positions.append({
                    "currency":  coin,
                    "amount":    round(amount, 8),
                    "price_usd": round(price, 2) if price else None,
                    "value_usd": value,
                })

            position_value = sum((p["value_usd"] or 0) for p in positions)
            return {
                "account_value":  round(usdt_total + position_value, 2),
                "total_usdt":     round(usdt_total, 2),
                "free_usdt":      round(usdt_free, 2),
                "funding_usdt":   0.0,
                "position_value": round(position_value, 2),
                "open_positions": len(coin_amounts),
                "positions":      positions,
                "exchange":       "binance",
                "testnet":        self.testnet,
            }
        except Exception as e:
            logger.error(f"Binance balance error: {e}")
            return {"error": str(e)}

    # ── ORDER PLACEMENT ───────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        usdt_amount: float,
        current_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> dict:
        if not self.exchange or not self._has_private:
            return {"status": "ERROR", "reason": "Binance not connected"}

        self._ensure_markets()

        try:
            if side.upper() == "BUY":
                order = self.exchange.create_market_buy_order(
                    symbol, None,
                    params={"quoteOrderQty": usdt_amount},
                )
                fill_price  = float(order.get("average") or order.get("price") or current_price)
                base_filled = float(order.get("filled") or (usdt_amount / fill_price))

                try:
                    sl_price = self.exchange.price_to_precision(symbol, stop_loss_price)
                    self.exchange.create_order(
                        symbol, "STOP_LOSS_LIMIT", "sell",
                        base_filled,
                        price=float(sl_price),
                        params={"stopPrice": float(sl_price), "timeInForce": "GTC"},
                    )
                except Exception as sl_err:
                    logger.warning(f"Binance stop-loss order failed (main order still placed): {sl_err}")

                return {
                    "status":      "SUBMITTED",
                    "exchange":    "binance",
                    "testnet":     self.testnet,
                    "symbol":      symbol,
                    "side":        "BUY",
                    "base_amount": round(base_filled, 6),
                    "usdt_spent":  round(usdt_amount, 2),
                    "fill_price":  round(fill_price, 6),
                    "stop_loss":   stop_loss_price,
                    "take_profit": take_profit_price,
                    "order_id":    order.get("id"),
                }

            else:
                base_amount = float(usdt_amount / current_price)
                base_amount = float(self.exchange.amount_to_precision(symbol, base_amount))
                order = self.exchange.create_market_sell_order(symbol, base_amount)
                fill_price = float(order.get("average") or order.get("price") or current_price)
                return {
                    "status":        "SUBMITTED",
                    "exchange":      "binance",
                    "testnet":       self.testnet,
                    "symbol":        symbol,
                    "side":          "SELL",
                    "base_amount":   round(base_amount, 6),
                    "usdt_received": round(base_amount * fill_price, 2),
                    "fill_price":    round(fill_price, 6),
                    "order_id":      order.get("id"),
                }

        except Exception as e:
            logger.error(f"Binance order error: {e}")
            return {"status": "ERROR", "reason": str(e), "symbol": symbol}
