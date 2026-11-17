"""Market data tools backed by yfinance.

Each tool is an @beta_async_tool so the Anthropic tool runner can call it
directly. yfinance is synchronous, so implementations run in a thread.
All tools return JSON strings; failures return {"error": ...} rather than
raising, so the agent can note the gap and continue.
"""

import asyncio
import functools
import json
import math
from datetime import datetime, timezone

import yfinance as yf
from anthropic import beta_async_tool

from .config import TOOL_RESULT_CHAR_LIMIT


# ------------------------------------------------------------ helpers


@functools.lru_cache(maxsize=64)
def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol.upper().strip())


def _clean(obj):
    """Convert pandas/numpy artifacts into JSON-safe values."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 6)
    if hasattr(obj, "item"):  # numpy scalar
        return _clean(obj.item())
    if hasattr(obj, "isoformat"):  # Timestamp / datetime / date
        return obj.isoformat()
    return obj


def _dumps(obj) -> str:
    text = json.dumps(_clean(obj), default=str)
    if len(text) > TOOL_RESULT_CHAR_LIMIT:
        text = text[:TOOL_RESULT_CHAR_LIMIT] + " ...[TRUNCATED - result too large]"
    return text


def _frame_to_records(df, limit=20):
    if df is None or getattr(df, "empty", True):
        return []
    df = df.reset_index()
    return df.head(limit).to_dict(orient="records")


def _guard(fn, symbol, **kwargs):
    try:
        return _dumps(fn(symbol, **kwargs))
    except Exception as exc:  # network errors, missing data, schema drift
        return _dumps({"error": f"{type(exc).__name__}: {exc}", "ticker": symbol})


# --------------------------------------------------- sync implementations


_OVERVIEW_FIELDS = [
    "longName", "sector", "industry", "country", "marketCap", "currency",
    "currentPrice", "sharesOutstanding", "floatShares", "heldPercentInsiders",
    "heldPercentInstitutions", "fullTimeEmployees", "longBusinessSummary",
]

_FUNDAMENTAL_FIELDS = [
    "currentPrice", "marketCap", "enterpriseValue",
    "trailingPE", "forwardPE", "pegRatio", "trailingPegRatio",
    "priceToSalesTrailing12Months", "priceToBook", "enterpriseToRevenue",
    "enterpriseToEbitda",
    "grossMargins", "operatingMargins", "profitMargins", "ebitdaMargins",
    "returnOnEquity", "returnOnAssets",
    "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth",
    "totalRevenue", "ebitda", "netIncomeToCommon",
    "totalCash", "totalDebt", "debtToEquity", "quickRatio", "currentRatio",
    "freeCashflow", "operatingCashflow",
    "trailingEps", "forwardEps",
    "dividendYield", "payoutRatio",
    "beta", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
]


def _pick(info: dict, fields: list[str]) -> dict:
    return {f: info.get(f) for f in fields if info.get(f) is not None}


def _company_overview(symbol: str) -> dict:
    info = _ticker(symbol).info or {}
    out = _pick(info, _OVERVIEW_FIELDS)
    summary = out.get("longBusinessSummary")
    if isinstance(summary, str) and len(summary) > 1200:
        out["longBusinessSummary"] = summary[:1200] + "..."
    return out or {"error": "no overview data returned"}


def _fundamentals(symbol: str) -> dict:
    info = _ticker(symbol).info or {}
    out = _pick(info, _FUNDAMENTAL_FIELDS)
    return out or {"error": "no fundamental data returned"}


def _analyst_data(symbol: str) -> dict:
    t = _ticker(symbol)
    out: dict = {}
    for name, attr in [
        ("price_targets", "analyst_price_targets"),
        ("recommendations_summary", "recommendations_summary"),
        ("earnings_estimate", "earnings_estimate"),
        ("revenue_estimate", "revenue_estimate"),
        ("eps_trend", "eps_trend"),
        ("eps_revisions", "eps_revisions"),
    ]:
        try:
            val = getattr(t, attr)
            if hasattr(val, "empty"):
                out[name] = _frame_to_records(val, limit=8)
            else:
                out[name] = val
        except Exception as exc:
            out[name] = f"unavailable ({type(exc).__name__})"
    return out


def _price_history(symbol: str, period: str = "1y") -> dict:
    hist = _ticker(symbol).history(period=period, auto_adjust=True)
    if hist is None or hist.empty:
        return {"error": f"no price history for period={period}"}
    close = hist["Close"]
    last = float(close.iloc[-1])

    def ret(days: int):
        base_idx = -days - 1 if len(close) > days else 0
        return round((last / float(close.iloc[base_idx]) - 1) * 100, 2)

    daily = close.pct_change().dropna()
    running_max = close.cummax()
    drawdown = (close / running_max - 1).min()
    out = {
        "period": period,
        "last_close": round(last, 2),
        "return_pct": {"1m": ret(21), "3m": ret(63), "6m": ret(126), "12m": ret(252)},
        "high": round(float(close.max()), 2),
        "low": round(float(close.min()), 2),
        "pct_off_high": round((last / float(close.max()) - 1) * 100, 2),
        "annualized_volatility_pct": round(float(daily.std()) * math.sqrt(252) * 100, 2),
        "max_drawdown_pct": round(float(drawdown) * 100, 2),
        "avg_daily_dollar_volume": round(float((hist["Close"] * hist["Volume"]).tail(63).mean()), 0),
    }
    if len(close) >= 50:
        out["sma_50"] = round(float(close.tail(50).mean()), 2)
        out["price_vs_sma50_pct"] = round((last / out["sma_50"] - 1) * 100, 2)
    if len(close) >= 200:
        out["sma_200"] = round(float(close.tail(200).mean()), 2)
        out["price_vs_sma200_pct"] = round((last / out["sma_200"] - 1) * 100, 2)
    return out


def _options_chain(symbol: str, min_months_out: int = 10) -> dict:
    t = _ticker(symbol)
    expirations = list(t.options or [])
    if not expirations:
        return {"error": "no listed options"}
    now = datetime.now(timezone.utc)
    spot = None
    try:
        spot = float(t.fast_info["lastPrice"])
    except Exception:
        info = t.info or {}
        spot = info.get("currentPrice")

    def months_out(date_str: str) -> float:
        exp = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (exp - now).days / 30.44

    leaps = [e for e in expirations if months_out(e) >= min_months_out]
    chosen = leaps[:2] if leaps else expirations[-2:]
    out = {"spot": spot, "all_expirations": expirations, "chains": []}
    for exp in chosen:
        try:
            calls = t.option_chain(exp).calls
            if spot:
                calls = calls[(calls["strike"] >= spot * 0.7) & (calls["strike"] <= spot * 1.35)]
            cols = [c for c in ["strike", "lastPrice", "bid", "ask", "impliedVolatility",
                                "volume", "openInterest", "inTheMoney"] if c in calls.columns]
            out["chains"].append({
                "expiration": exp,
                "months_out": round(months_out(exp), 1),
                "calls": calls[cols].head(18).to_dict(orient="records"),
                "note": "yfinance provides no greeks; estimate delta from moneyness/IV",
            })
        except Exception as exc:
            out["chains"].append({"expiration": exp, "error": str(exc)})
    return out


def _news(symbol: str) -> dict:
    items = _ticker(symbol).news or []
    parsed = []
    for item in items[:12]:
        content = item.get("content", item)  # newer yfinance nests under "content"
        parsed.append({
            "title": content.get("title"),
            "publisher": (content.get("provider") or {}).get("displayName")
            if isinstance(content.get("provider"), dict) else content.get("publisher"),
            "published": content.get("pubDate") or content.get("providerPublishTime"),
            "summary": (content.get("summary") or "")[:300] or None,
        })
    return {"news": parsed} if parsed else {"error": "no recent news returned"}


def _earnings_info(symbol: str) -> dict:
    t = _ticker(symbol)
    out: dict = {}
    try:
        cal = t.calendar
        out["calendar"] = _clean(dict(cal)) if isinstance(cal, dict) else _frame_to_records(cal)
    except Exception as exc:
        out["calendar"] = f"unavailable ({type(exc).__name__})"
    try:
        out["recent_earnings_dates"] = _frame_to_records(t.earnings_dates, limit=8)
    except Exception as exc:
        out["recent_earnings_dates"] = f"unavailable ({type(exc).__name__})"
    return out


def _insider_activity(symbol: str) -> dict:
    t = _ticker(symbol)
    try:
        return {"insider_transactions": _frame_to_records(t.insider_transactions, limit=15)}
    except Exception as exc:
        return {"error": f"unavailable ({type(exc).__name__}: {exc})"}


# ------------------------------------------------------------- tools


@beta_async_tool
async def get_company_overview(ticker: str) -> str:
    """Get the company profile: name, sector, industry, market cap, share statistics, and business summary.

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
    """
    return await asyncio.to_thread(_guard, _company_overview, ticker)


@beta_async_tool
async def get_fundamentals(ticker: str) -> str:
    """Get valuation multiples, margins, growth rates, returns, balance-sheet items, and cash flow figures.

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
    """
    return await asyncio.to_thread(_guard, _fundamentals, ticker)


@beta_async_tool
async def get_analyst_data(ticker: str) -> str:
    """Get Wall Street analyst data: price targets, recommendation counts, EPS/revenue estimates, EPS trend, and EPS revisions (up/down counts). Use this to judge estimate-revision momentum.

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
    """
    return await asyncio.to_thread(_guard, _analyst_data, ticker)


@beta_async_tool
async def get_price_history(ticker: str, period: str = "1y") -> str:
    """Get price/technical summary: trailing returns, 50/200-day moving averages vs price, volatility, max drawdown, and dollar volume.

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
        period: History window - one of "6mo", "1y", "2y", "5y".
    """
    return await asyncio.to_thread(_guard, _price_history, ticker, period=period)


@beta_async_tool
async def get_options_chain(ticker: str, min_months_out: int = 10) -> str:
    """Get LEAPS-focused call option chains: the nearest expirations at least min_months_out months away, with strikes within roughly +/-30% of spot (price, bid/ask, IV, volume, open interest).

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
        min_months_out: Minimum months to expiration for the chains returned (default 10).
    """
    return await asyncio.to_thread(_guard, _options_chain, ticker, min_months_out=min_months_out)


@beta_async_tool
async def get_news(ticker: str) -> str:
    """Get recent news headlines and short summaries for the company.

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
    """
    return await asyncio.to_thread(_guard, _news, ticker)


@beta_async_tool
async def get_earnings_info(ticker: str) -> str:
    """Get the upcoming earnings date and recent quarterly EPS estimates vs actuals (surprise history).

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
    """
    return await asyncio.to_thread(_guard, _earnings_info, ticker)


@beta_async_tool
async def get_insider_activity(ticker: str) -> str:
    """Get recent insider transactions (buys/sells by officers and directors).

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
    """
    return await asyncio.to_thread(_guard, _insider_activity, ticker)


MARKET_TOOLS = [
    get_company_overview,
    get_fundamentals,
    get_analyst_data,
    get_price_history,
    get_options_chain,
    get_news,
    get_earnings_info,
    get_insider_activity,
]
