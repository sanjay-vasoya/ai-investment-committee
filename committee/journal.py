"""Append-only decision journal (JSONL)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import JOURNAL_PATH
from .orchestrator import CommitteeResult


def append_entry(result: CommitteeResult, report_path: Path) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    v = result.verdict
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": result.ticker,
        "decision": v.decision,
        "conviction_total": v.conviction.total,
        "confidence": v.confidence,
        "prob_outperform_sp500_pct": v.probability_of_outperforming_sp500_pct,
        "expected_upside_pct": v.expected_upside_pct,
        "expected_downside_pct": v.expected_downside_pct,
        "decision_journal": v.decision_journal.model_dump(),
        "report_path": str(report_path),
        "agent_errors": result.errors,
        "portfolio_review": result.portfolio.model_dump() if result.portfolio else None,
    }
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_entries(ticker: str | None = None) -> list[dict]:
    if not JOURNAL_PATH.exists():
        return []
    entries = []
    for line in JOURNAL_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if ticker is None or entry.get("ticker") == ticker.upper():
            entries.append(entry)
    return entries
