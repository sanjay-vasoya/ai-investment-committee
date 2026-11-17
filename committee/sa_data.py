"""Seeking Alpha xlsx ingestion and lookup.

Seeking Alpha has no API, so the operator manually exports screener xlsx
files. Files are parsed with calamine (SA's exports contain conditional-
formatting XML that openpyxl rejects), normalized header-driven (column sets
vary by screen), and merged into a per-ticker JSON store — latest upload
wins per ticker. Agents read the store through the get_seeking_alpha_data
tool.
"""

import asyncio
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from anthropic import beta_async_tool

from .config import DATA_DIR

SA_DIR = DATA_DIR / "seeking_alpha"
SA_STORE = SA_DIR / "store.json"

_SYMBOL_HEADERS = {"symbol", "ticker"}
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _norm_key(header: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(header).strip().lower()).strip("_")
    return key or "column"


def _norm_value(value):
    if isinstance(value, str):
        value = value.strip()
        if value in ("", "-", "NM", "N/A"):
            return None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


def load_store() -> dict:
    if SA_STORE.exists():
        try:
            return json.loads(SA_STORE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"tickers": {}, "files": [], "updated_at": None}


def _save_store(store: dict) -> None:
    SA_DIR.mkdir(parents=True, exist_ok=True)
    store["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    SA_STORE.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def _file_as_of(filename: str, fallback: str) -> str:
    m = _DATE_RE.search(filename)
    return m.group(1) if m else fallback


def ingest_xlsx(path: Path, original_name: str | None = None) -> dict:
    """Parse an SA export and merge its rows into the store (latest wins
    per ticker). Returns an ingest summary. Raises ValueError on files
    without a recognizable Symbol column."""
    from python_calamine import CalamineWorkbook

    name = original_name or path.name
    as_of = _file_as_of(name, date.today().isoformat())
    wb = CalamineWorkbook.from_path(str(path))

    parsed: dict[str, dict] = {}
    columns: list[str] = []
    for sheet_name in wb.sheet_names:
        rows = wb.get_sheet_by_name(sheet_name).to_python()
        if not rows:
            continue
        headers = [_norm_key(h) for h in rows[0]]
        try:
            sym_idx = next(i for i, h in enumerate(headers) if h in _SYMBOL_HEADERS)
        except StopIteration:
            continue  # not a screener sheet
        columns = headers
        for row in rows[1:]:
            if sym_idx >= len(row) or not str(row[sym_idx]).strip():
                continue
            symbol = str(row[sym_idx]).strip().upper()
            metrics = {
                headers[i]: _norm_value(row[i])
                for i in range(min(len(headers), len(row)))
                if headers[i] not in _SYMBOL_HEADERS and headers[i] != "rank"
            }
            parsed[symbol] = metrics
        break  # first screener-shaped sheet wins

    if not parsed:
        raise ValueError(
            "no sheet with a Symbol/Ticker column found — is this a "
            "Seeking Alpha screener export?"
        )

    store = load_store()
    for symbol, metrics in parsed.items():
        store["tickers"][symbol] = {
            "as_of": as_of,
            "source_file": name,
            "metrics": metrics,
        }
    store["files"] = ([{"name": name, "as_of": as_of, "tickers": sorted(parsed)}]
                      + [f for f in store.get("files", []) if f.get("name") != name])[:20]
    _save_store(store)

    return {
        "file": name,
        "as_of": as_of,
        "tickers_in_file": sorted(parsed),
        "columns": columns,
        "tickers_in_store": len(store["tickers"]),
    }


def get_ticker_data(symbol: str) -> dict | None:
    return load_store()["tickers"].get(symbol.upper().strip())


def store_summary() -> dict:
    store = load_store()
    tickers = store["tickers"]
    latest = max((t["as_of"] for t in tickers.values()), default=None)
    return {
        "ticker_count": len(tickers),
        "tickers": sorted(tickers),
        "latest_as_of": latest,
        "updated_at": store.get("updated_at"),
        "files": store.get("files", []),
    }


def holdings_snapshot(symbols: list[str]) -> dict[str, dict]:
    """SA records for the given symbols (for the portfolio manager prompt)."""
    tickers = load_store()["tickers"]
    return {s: tickers[s] for s in (x.upper() for x in symbols) if s in tickers}


def _age_days(as_of: str) -> int | None:
    try:
        return (date.today() - date.fromisoformat(as_of)).days
    except ValueError:
        return None


def _lookup(ticker: str) -> str:
    record = get_ticker_data(ticker)
    if record is None:
        available = sorted(load_store()["tickers"])
        return json.dumps({
            "error": f"no Seeking Alpha data for {ticker.upper()} in the operator's uploads",
            "tickers_available": available,
        })
    age = _age_days(record["as_of"])
    return json.dumps({
        "ticker": ticker.upper(),
        "as_of": record["as_of"],
        "age_days": age,
        "stale": bool(age is not None and age > 7),
        "source_file": record["source_file"],
        "metrics": record["metrics"],
        "note": "Manually exported screener data — not real-time. Grades are "
                "Seeking Alpha factor grades (A+ best to F). quant_rating is "
                "SA's 1-5 quant score.",
    })


@beta_async_tool
async def get_seeking_alpha_data(ticker: str) -> str:
    """Get the operator's imported Seeking Alpha screener data for a ticker: SA Quant Rating (1-5), analyst/Wall Street ratings, and factor grades (valuation, growth, profitability, momentum, EPS revisions), plus any raw metrics in the export. Data comes from manual xlsx uploads and may be days old - check as_of/stale. Cross-check overlapping metrics against live sources and report disagreements.

    Args:
        ticker: Stock ticker symbol, e.g. NVDA.
    """
    return await asyncio.to_thread(_lookup, ticker)
