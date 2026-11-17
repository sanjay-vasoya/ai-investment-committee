"""Render a CommitteeResult as a markdown report."""

from pathlib import Path

from .config import DISCLAIMER, REPORTS_DIR
from .orchestrator import CommitteeResult


def _scenario_row(name: str, s) -> str:
    return (f"| {name} | {s.probability_pct:.0f}% | {s.twelve_month_price_view} | "
            f"{s.narrative} |")


def render_markdown(result: CommitteeResult) -> str:
    v = result.verdict
    c = v.conviction
    lines: list[str] = []
    add = lines.append

    add(f"# Committee Report: {result.ticker} — {result.run_date}")
    add("")
    add(f"> {DISCLAIMER}")
    add("")
    add(f"## Decision: **{v.decision}**  (conviction {c.total:.0f}/100, "
        f"confidence: {v.confidence})")
    add("")
    add(v.decision_rationale)
    add("")
    add("## Executive Summary")
    add("")
    add(v.executive_summary)
    add("")
    add("## Conviction Score")
    add("")
    add("| Component | Score | Max |")
    add("|---|---|---|")
    add(f"| Growth | {c.growth:.1f} | 30 |")
    add(f"| Estimate revisions | {c.estimate_revisions:.1f} | 25 |")
    add(f"| Profitability | {c.profitability:.1f} | 20 |")
    add(f"| Valuation | {c.valuation:.1f} | 15 |")
    add(f"| Business quality | {c.business_quality:.1f} | 10 |")
    for d in c.deductions:
        add(f"| Deduction: {d.reason} | -{abs(d.points):.1f} | |")
    add(f"| **Total** | **{c.total:.1f}** | **100** |")
    add("")
    add("## Scenarios (12–24 months)")
    add("")
    add("| Case | Probability | Price view | Narrative |")
    add("|---|---|---|---|")
    add(_scenario_row("Bull", v.bull_case))
    add(_scenario_row("Base", v.base_case))
    add(_scenario_row("Bear", v.bear_case))
    add("")
    add(f"- Probability of outperforming the S&P 500: "
        f"**{v.probability_of_outperforming_sp500_pct:.0f}%**")
    add(f"- Expected upside: **{v.expected_upside_pct:+.0f}%** · "
        f"Expected downside: **{v.expected_downside_pct:+.0f}%**")
    add("")

    if result.quant:
        q = result.quant
        add("## Quant Analyst")
        add("")
        add("| Factor | Score /10 |")
        add("|---|---|")
        add(f"| Growth | {q.growth_score:.1f} |")
        add(f"| Profitability | {q.profitability_score:.1f} |")
        add(f"| Momentum | {q.momentum_score:.1f} |")
        add(f"| Value | {q.value_score:.1f} |")
        add(f"| Estimate revisions | {q.estimate_revisions_score:.1f} "
            f"({q.estimate_revision_direction}) |")
        add(f"| Technical trend | {q.technical_trend_score:.1f} |")
        add("")
        if q.key_metrics:
            add("Key metrics: " + "; ".join(f"{m.name}: {m.value}" for m in q.key_metrics))
            add("")
        add(q.commentary)
        if q.data_mismatches:
            add("")
            add("**Data source mismatches:**")
            for m in q.data_mismatches:
                add(f"- {m}")
        if q.data_gaps:
            add("")
            add("Data gaps: " + "; ".join(q.data_gaps))
        add("")

    if result.business:
        b = result.business
        add("## Business Analyst")
        add("")
        add(f"**Moat: {b.moat}** · Business quality: {b.business_quality_score:.1f}/10")
        add("")
        add(f"- **Moat:** {b.moat_analysis}")
        add(f"- **Management:** {b.management_assessment}")
        add(f"- **Industry & tailwinds:** {b.industry_and_tailwinds}")
        add(f"- **Execution risk:** {b.execution_risk}")
        add(f"- **Customer concentration:** {b.customer_concentration}")
        add(f"- **Pricing power:** {b.pricing_power}")
        add("")
        add(b.summary)
        add("")

    if result.risk:
        r = result.risk
        add("## Risk Manager")
        add("")
        add(f"**Overall risk: {r.overall_risk}** · Probability of permanent "
            f"capital loss: **{r.probability_of_permanent_capital_loss}**")
        add("")
        add(f"- **Balance sheet:** {r.balance_sheet}")
        add(f"- **Debt:** {r.debt}")
        add(f"- **Dilution:** {r.dilution}")
        add(f"- **Regulatory:** {r.regulatory}")
        add(f"- **Execution:** {r.execution}")
        add(f"- **Geopolitical:** {r.geopolitical}")
        add(f"- **Valuation risk:** {r.valuation_risk}")
        add("")
        add(f"**Worst case:** {r.worst_case_scenario}")
        if r.red_flags:
            add("")
            add("Red flags: " + "; ".join(r.red_flags))
        add("")

    if result.options:
        o = result.options
        add("## Options Specialist")
        add("")
        add(f"**LEAPS appropriate: {'yes' if o.leaps_appropriate else 'no'}**")
        add("")
        add(f"- **Stock vs LEAPS:** {o.stock_vs_leaps}")
        add(f"- **Rationale:** {o.rationale}")
        add(f"- **Liquidity:** {o.liquidity_notes}")
        if o.leaps_idea:
            i = o.leaps_idea
            add("")
            add(f"**Structure analyzed:** {i.strategy}")
            add(f"- Strike: {i.strike_rationale}")
            add(f"- Expiration: {i.expiration_rationale}")
            add(f"- Estimated max loss: {i.estimated_max_loss}")
            add(f"- Probability-of-success view: {i.probability_of_success_view}")
            add(f"- Greeks notes: {i.greeks_notes}")
        add("")

    if result.portfolio:
        p = result.portfolio
        add("## Portfolio Manager")
        add("")
        if p.holdings:
            add("| Holding | Verdict | Rationale |")
            add("|---|---|---|")
            for h in p.holdings:
                add(f"| {h.ticker} | {h.verdict} | {h.rationale} |")
            add("")
        add(f"- **Candidate fit:** {p.candidate_fit}")
        add(f"- **Overlap:** {p.overlap_concerns}")
        add(f"- **Sector exposure:** {p.sector_exposure_notes}")
        add(f"- **Replacement suggestion:** {p.replacement_suggestion}")
        add(f"- **Cash deployment:** {p.cash_deployment_note}")
        add("")

    add("## LEAPS View (Chair)")
    add("")
    add(v.leaps_view)
    add("")
    add("## Replacement Analysis")
    add("")
    add(v.replacement_analysis)
    add("")
    add("## Market Risks")
    add("")
    for risk in v.market_risks:
        add(f"- {risk}")
    add("")
    add("## Catalysts & Earnings")
    add("")
    for cat in v.key_catalysts:
        add(f"- {cat}")
    add(f"- Upcoming earnings: {v.upcoming_earnings}")
    add("")
    add("## Decision Journal")
    add("")
    j = v.decision_journal
    add(f"- **Why buy?** {j.why_buy}")
    add(f"- **Why now?** {j.why_now}")
    add(f"- **Why not wait?** {j.why_not_wait}")
    add(f"- **What could prove this thesis wrong?** {j.what_could_prove_thesis_wrong}")
    add(f"- **When to sell?** {j.when_to_sell}")
    add(f"- **When to average down?** {j.when_to_average_down}")
    add("")

    if result.errors:
        add("## Pipeline Warnings")
        add("")
        for agent, err in result.errors.items():
            add(f"- `{agent}` agent failed: {err}")
        add("")

    return "\n".join(lines)


def save_report(result: CommitteeResult) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{result.ticker}_{result.run_date}.md"
    path.write_text(render_markdown(result), encoding="utf-8")
    return path
