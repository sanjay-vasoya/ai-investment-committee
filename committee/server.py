"""FastAPI server exposing the committee pipeline to a local web UI.

Run with:  python -m committee serve  (binds 127.0.0.1 only)
"""

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import markdown as md_lib
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import sa_data
from .config import DISCLAIMER, PORTFOLIO_PATH, REPORTS_DIR
from .journal import append_entry, load_entries
from .orchestrator import analyze_ticker
from .report import save_report

WEB_DIR = Path(__file__).parent / "web"
_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")
_REPORT_NAME_RE = re.compile(r"^[A-Z0-9.\-]{1,12}_\d{4}-\d{2}-\d{2}\.md$")

app = FastAPI(title="AI Investment Committee", docs_url=None, redoc_url=None)

# In-memory run registry (per server process)
RUNS: dict[str, dict] = {}


class AnalyzeRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=5)
    use_portfolio: bool = True
    use_seeking_alpha: bool = True


def _load_portfolio() -> Optional[dict]:
    if PORTFOLIO_PATH.exists():
        try:
            return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


async def _execute_run(run_id: str) -> None:
    state = RUNS[run_id]
    log = state["log"].append
    portfolio = _load_portfolio() if state["use_portfolio"] else None
    if state["use_portfolio"] and portfolio is None:
        log("note: no readable data/portfolio.json — portfolio review skipped.")

    for ticker in state["tickers"]:
        try:
            result = await analyze_ticker(
                ticker,
                portfolio=portfolio,
                use_seeking_alpha=state["use_seeking_alpha"],
                progress=log,
            )
            path = save_report(result)
            append_entry(result, path)
            state["reports"].append(path.name)
            log(f"[{ticker}] report saved: {path.name}")
        except Exception as exc:
            state["errors"][ticker] = f"{type(exc).__name__}: {exc}"
            log(f"[{ticker}] FAILED: {state['errors'][ticker]}")

    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    if state["reports"] and not state["errors"]:
        state["status"] = "done"
    elif state["reports"]:
        state["status"] = "done_with_errors"
    else:
        state["status"] = "failed"


@app.post("/api/analyze")
async def start_analysis(req: AnalyzeRequest):
    tickers = [t.strip().upper() for t in req.tickers if t.strip()]
    for t in tickers:
        if not _TICKER_RE.match(t):
            raise HTTPException(400, f"invalid ticker: {t!r}")
    if not tickers:
        raise HTTPException(400, "no tickers given")
    if any(r["status"] == "running" for r in RUNS.values()):
        raise HTTPException(409, "a run is already in progress — wait for it to finish")

    run_id = uuid.uuid4().hex[:12]
    RUNS[run_id] = {
        "id": run_id,
        "tickers": tickers,
        "use_portfolio": req.use_portfolio,
        "use_seeking_alpha": req.use_seeking_alpha,
        "status": "running",
        "log": [f"run started for {', '.join(tickers)}"],
        "reports": [],
        "errors": {},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    RUNS[run_id]["task"] = asyncio.create_task(_execute_run(run_id))
    return _public_run(RUNS[run_id])


def _public_run(state: dict) -> dict:
    return {k: v for k, v in state.items() if k != "task"}


@app.get("/api/runs")
async def list_runs():
    runs = sorted(RUNS.values(), key=lambda r: r["started_at"], reverse=True)
    return [_public_run(r) for r in runs[:20]]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "unknown run")
    return _public_run(state)


@app.get("/api/reports")
async def list_reports():
    if not REPORTS_DIR.exists():
        return []
    files = sorted(REPORTS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files:
        ticker, _, rest = p.stem.partition("_")
        out.append({"name": p.name, "ticker": ticker, "date": rest})
    return out


@app.get("/api/reports/{name}")
async def get_report(name: str):
    if not _REPORT_NAME_RE.match(name):
        raise HTTPException(400, "bad report name")
    path = REPORTS_DIR / name
    if not path.exists():
        raise HTTPException(404, "report not found")
    text = path.read_text(encoding="utf-8")
    html = md_lib.markdown(text, extensions=["tables", "sane_lists"])
    return {"name": name, "markdown": text, "html": html}


@app.get("/api/journal")
async def get_journal():
    return list(reversed(load_entries()))


@app.get("/api/meta")
async def get_meta():
    return {
        "disclaimer": DISCLAIMER,
        "portfolio_configured": PORTFOLIO_PATH.exists(),
    }


@app.post("/api/sa-upload")
async def upload_seeking_alpha(file: UploadFile):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "expected a .xlsx Seeking Alpha export")
    safe_name = re.sub(r"[^A-Za-z0-9. _-]", "", file.filename)[-80:] or "upload.xlsx"
    sa_data.SA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = sa_data.SA_DIR / f"{stamp}_{safe_name}"
    dest.write_bytes(await file.read())
    try:
        summary = sa_data.ingest_xlsx(dest, original_name=safe_name)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"could not parse file: {exc}")
    return summary


@app.get("/api/sa-status")
async def sa_status():
    return sa_data.store_summary()


@app.get("/api/portfolio-view")
async def portfolio_view():
    if not PORTFOLIO_PATH.exists():
        return {"portfolio_configured": False, "rows": []}
    portfolio = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))

    # Latest PM verdict per holding across all journal entries (chronological
    # order means later entries overwrite earlier ones).
    verdicts: dict[str, dict] = {}
    for entry in load_entries():
        review = entry.get("portfolio_review")
        if not review:
            continue
        for holding in review.get("holdings", []):
            verdicts[holding["ticker"].upper()] = {
                "verdict": holding["verdict"],
                "rationale": holding["rationale"],
                "reviewed_in": entry["ticker"],
                "date": (entry.get("timestamp") or "")[:10],
                "report": Path(entry.get("report_path", "")).name,
            }

    sa_tickers = sa_data.load_store()["tickers"]
    rows = []
    for pos in portfolio.get("positions", []):
        ticker = str(pos.get("ticker", "")).upper()
        sa_record = sa_tickers.get(ticker)
        rows.append({
            "ticker": ticker,
            "type": pos.get("type", "equity"),
            "quantity": pos.get("shares", pos.get("contracts")),
            "cost_basis": pos.get("cost_basis_per_share", pos.get("cost_basis_per_contract")),
            "detail": (f"{pos.get('strike')}C {pos.get('expiration')}"
                       if pos.get("type") == "leaps_call" else ""),
            "sa_quant_rating": (sa_record or {}).get("metrics", {}).get("quant_rating"),
            **(verdicts.get(ticker) or {"verdict": "unreviewed", "rationale": "",
                                        "reviewed_in": "", "date": "", "report": ""}),
        })
    return {
        "portfolio_configured": True,
        "cash": portfolio.get("cash"),
        "as_of": portfolio.get("as_of"),
        "rows": rows,
    }


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")
