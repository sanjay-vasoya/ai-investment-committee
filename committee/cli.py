"""Command-line interface.

Usage:
    python -m committee analyze NVDA [MSFT ...] [--no-portfolio] [--portfolio PATH]
    python -m committee journal [TICKER]
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .config import DISCLAIMER, PORTFOLIO_PATH
from .journal import append_entry, load_entries
from .orchestrator import analyze_ticker
from .report import save_report


def _load_portfolio(path: Path) -> dict | None:
    if not path.exists():
        print(f"note: no portfolio file at {path} — skipping portfolio review.")
        print("      (copy data/portfolio.example.json to data/portfolio.json to enable it)")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


async def _run_analyze(args) -> int:
    portfolio = None if args.no_portfolio else _load_portfolio(Path(args.portfolio))
    failures = 0
    for ticker in args.tickers:
        print(f"\n=== {ticker.upper()} ===")
        try:
            result = await analyze_ticker(
                ticker,
                portfolio=portfolio,
                use_seeking_alpha=not args.no_seeking_alpha,
            )
        except Exception as exc:
            print(f"[{ticker}] pipeline failed: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        path = save_report(result)
        append_entry(result, path)
        print(f"[{ticker}] report saved to {path}")
    return 1 if failures else 0


def _run_journal(args) -> int:
    entries = load_entries(args.ticker)
    if not entries:
        print("no journal entries yet.")
        return 0
    for e in entries:
        print(f"{e['timestamp'][:10]}  {e['ticker']:<6} {e['decision']:<6} "
              f"conviction={e['conviction_total']:.0f}  ({e['report_path']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="committee",
        description="AI Investment Committee — multi-agent research pipeline. "
                    "Output is research, not financial advice.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Run the full committee on one or more tickers")
    p_analyze.add_argument("tickers", nargs="+", help="Ticker symbols, e.g. NVDA MSFT")
    p_analyze.add_argument("--portfolio", default=str(PORTFOLIO_PATH),
                           help="Path to portfolio JSON (default: data/portfolio.json)")
    p_analyze.add_argument("--no-portfolio", action="store_true",
                           help="Skip the portfolio manager agent")
    p_analyze.add_argument("--no-seeking-alpha", action="store_true",
                           help="Ignore uploaded Seeking Alpha data")

    p_journal = sub.add_parser("journal", help="List past decision-journal entries")
    p_journal.add_argument("ticker", nargs="?", default=None)

    p_serve = sub.add_parser("serve", help="Start the local web dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int,
                         default=int(os.environ.get("PORT", "8787")))

    args = parser.parse_args(argv)

    if args.command == "analyze":
        print(DISCLAIMER)
        print("note: each ticker runs ~6 Claude Opus calls with tool use — "
              "expect a few minutes and real API cost per ticker.")
        return asyncio.run(_run_analyze(args))
    if args.command == "journal":
        return _run_journal(args)
    if args.command == "serve":
        import uvicorn

        print(f"AI Investment Committee dashboard: http://{args.host}:{args.port}")
        uvicorn.run("committee.server:app", host=args.host, port=args.port, log_level="warning")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
