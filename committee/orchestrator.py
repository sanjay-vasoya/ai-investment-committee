"""Runs the committee: parallel specialist agents -> portfolio manager -> chair."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from anthropic import AsyncAnthropic
from pydantic import ValidationError

from . import sa_data
from .agents import ANALYSTS, CHAIR_SYSTEM, PORTFOLIO, AgentSpec
from .config import EFFORT, MAX_TOKENS, MODEL
from .market_data import MARKET_TOOLS
from .schemas import (
    BusinessAnalysis,
    CommitteeVerdict,
    OptionsAnalysis,
    PortfolioReview,
    QuantAnalysis,
    RiskAnalysis,
    StrictModel,
)


@dataclass
class CommitteeResult:
    ticker: str
    run_date: str
    verdict: CommitteeVerdict
    quant: Optional[QuantAnalysis] = None
    business: Optional[BusinessAnalysis] = None
    risk: Optional[RiskAnalysis] = None
    options: Optional[OptionsAnalysis] = None
    portfolio: Optional[PortfolioReview] = None
    errors: dict[str, str] = field(default_factory=dict)


def _client() -> AsyncAnthropic:
    # Resolves credentials from ANTHROPIC_API_KEY or an `ant auth login` profile.
    return AsyncAnthropic()


def _output_schema(model_cls: type[StrictModel]) -> dict:
    return model_cls.model_json_schema()


def _extract_json(text: str, model_cls: type[StrictModel]) -> StrictModel:
    """Validate agent output; tolerate stray fences or prose around the JSON."""
    try:
        return model_cls.model_validate_json(text)
    except ValidationError:
        pass
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return model_cls.model_validate_json(stripped)
    except ValidationError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return model_cls.model_validate_json(text[start : end + 1])
    raise ValueError(f"could not parse agent output as {model_cls.__name__}")


async def _run_specialist(
    client: AsyncAnthropic, spec: AgentSpec, user_prompt: str, tools: list
) -> StrictModel:
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        max_iterations=12,  # cost guard: cap tool round-trips per agent
        thinking={"type": "adaptive"},
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": _output_schema(spec.schema)},
        },
        system=spec.system,
        tools=tools,
        messages=[{"role": "user", "content": user_prompt}],
    )
    last = None
    async for message in runner:
        last = message
    if last is None:
        raise RuntimeError("tool runner produced no message")
    text = next((b.text for b in last.content if b.type == "text"), None)
    if not text:
        raise RuntimeError(f"no text in final message (stop_reason={last.stop_reason})")
    return _extract_json(text, spec.schema)


def _analysis_prompt(ticker: str, today: str, sa_active: bool) -> str:
    prompt = (
        f"Today is {today}. Analyze {ticker.upper()} using your tools, then "
        f"produce your structured report."
    )
    if sa_active:
        prompt += (
            "\n\nThe operator has uploaded Seeking Alpha screener data — call "
            "get_seeking_alpha_data for this ticker. Use it alongside the live "
            "sources; where both report the same metric, compare them and note "
            "material disagreements (with both values). If the SA record is "
            "stale (>7 days) or the ticker is absent, say so and rely on live "
            "data."
        )
    return prompt


def _dump(model: Optional[StrictModel], error: Optional[str]) -> str:
    if model is not None:
        return model.model_dump_json(indent=2)
    return json.dumps({"UNAVAILABLE": error or "agent failed"})


async def _run_chair(
    client: AsyncAnthropic,
    ticker: str,
    today: str,
    sections: dict[str, str],
) -> CommitteeVerdict:
    parts = [f"Today is {today}. Candidate under analysis: {ticker.upper()}.\n"]
    for name, payload in sections.items():
        parts.append(f"=== {name} report ===\n{payload}\n")
    parts.append(
        "Synthesize the committee's verdict now. If any specialist report is "
        "UNAVAILABLE, reduce confidence accordingly and note it."
    )
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=CHAIR_SYSTEM,
        messages=[{"role": "user", "content": "\n".join(parts)}],
        output_format=CommitteeVerdict,
    )
    if response.parsed_output is None:
        raise RuntimeError(
            f"chair output failed to parse (stop_reason={response.stop_reason})"
        )
    return response.parsed_output


async def analyze_ticker(
    ticker: str,
    portfolio: Optional[dict[str, Any]] = None,
    use_seeking_alpha: bool = True,
    progress=print,
) -> CommitteeResult:
    """Run the full committee on one ticker. `portfolio` is the parsed
    portfolio.json dict, or None to skip the portfolio manager.
    `use_seeking_alpha` includes uploaded SA screener data when available."""
    client = _client()
    today = date.today().isoformat()
    sa_active = use_seeking_alpha and sa_data.store_summary()["ticker_count"] > 0
    if use_seeking_alpha and not sa_active:
        progress(f"[{ticker}] no Seeking Alpha data uploaded — running on live sources only.")
    elif sa_active:
        progress(f"[{ticker}] Seeking Alpha data enabled "
                 f"({sa_data.store_summary()['ticker_count']} tickers in store).")
    prompt = _analysis_prompt(ticker, today, sa_active)

    tools = list(MARKET_TOOLS)
    if sa_active:
        tools.append(sa_data.get_seeking_alpha_data)

    progress(f"[{ticker}] running quant, business, risk, and options agents in parallel...")
    outcomes = await asyncio.gather(
        *[_run_specialist(client, spec, prompt, tools) for spec in ANALYSTS],
        return_exceptions=True,
    )

    results: dict[str, Optional[StrictModel]] = {}
    errors: dict[str, str] = {}
    for spec, outcome in zip(ANALYSTS, outcomes):
        if isinstance(outcome, BaseException):
            errors[spec.key] = f"{type(outcome).__name__}: {outcome}"
            results[spec.key] = None
            progress(f"[{ticker}] {spec.role} FAILED: {errors[spec.key]}")
        else:
            results[spec.key] = outcome
            progress(f"[{ticker}] {spec.role} done.")

    pm_result: Optional[PortfolioReview] = None
    if portfolio is not None:
        progress(f"[{ticker}] running portfolio manager...")
        pm_prompt = (
            f"{prompt}\n\nOperator's current portfolio (JSON):\n"
            f"{json.dumps(portfolio, indent=2)}\n\n"
            "Specialist findings on the candidate so far:\n"
            f"Quant: {_dump(results.get('quant'), errors.get('quant'))}\n"
            f"Business: {_dump(results.get('business'), errors.get('business'))}\n"
            f"Risk: {_dump(results.get('risk'), errors.get('risk'))}"
        )
        if sa_active:
            holdings = [p.get("ticker", "") for p in portfolio.get("positions", [])]
            snapshot = sa_data.holdings_snapshot([h for h in holdings if h])
            if snapshot:
                pm_prompt += (
                    "\n\nSeeking Alpha screener data for holdings that appear "
                    "in the operator's uploads (manual export — check as_of):\n"
                    + json.dumps(snapshot, indent=1)
                )
        try:
            pm_result = await _run_specialist(client, PORTFOLIO, pm_prompt, tools)
            progress(f"[{ticker}] Portfolio Manager done.")
        except Exception as exc:
            errors["portfolio"] = f"{type(exc).__name__}: {exc}"
            progress(f"[{ticker}] Portfolio Manager FAILED: {errors['portfolio']}")

    progress(f"[{ticker}] chair synthesizing verdict...")
    sections = {
        "Quant Analyst": _dump(results.get("quant"), errors.get("quant")),
        "Business Analyst": _dump(results.get("business"), errors.get("business")),
        "Risk Manager": _dump(results.get("risk"), errors.get("risk")),
        "Options Specialist": _dump(results.get("options"), errors.get("options")),
    }
    if portfolio is not None:
        sections["Portfolio Manager"] = _dump(pm_result, errors.get("portfolio"))

    verdict = await _run_chair(client, ticker, today, sections)
    progress(f"[{ticker}] verdict: {verdict.decision} "
             f"(conviction {verdict.conviction.total:.0f}/100)")

    return CommitteeResult(
        ticker=ticker.upper(),
        run_date=today,
        verdict=verdict,
        quant=results.get("quant"),          # type: ignore[arg-type]
        business=results.get("business"),    # type: ignore[arg-type]
        risk=results.get("risk"),            # type: ignore[arg-type]
        options=results.get("options"),      # type: ignore[arg-type]
        portfolio=pm_result,
        errors=errors,
    )
