"""Pydantic schemas for structured agent outputs.

Kept deliberately free of numeric/string constraints (min/max, lengths):
the structured-outputs API does not support them, so scoring bounds are
enforced by prompt instructions instead.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- Quant


class MetricValue(StrictModel):
    name: str
    value: str


class QuantAnalysis(StrictModel):
    growth_score: float  # 0-10
    profitability_score: float  # 0-10
    momentum_score: float  # 0-10
    value_score: float  # 0-10
    estimate_revisions_score: float  # 0-10
    technical_trend_score: float  # 0-10
    estimate_revision_direction: Literal["improving", "flat", "deteriorating", "unknown"]
    key_metrics: list[MetricValue]
    commentary: str
    data_gaps: list[str]
    data_mismatches: list[str]  # disagreements between data sources, with both values


# ------------------------------------------------------------- Business


class BusinessAnalysis(StrictModel):
    moat: Literal["none", "narrow", "wide"]
    moat_analysis: str
    management_assessment: str
    industry_and_tailwinds: str
    execution_risk: str
    customer_concentration: str
    pricing_power: str
    business_quality_score: float  # 0-10
    summary: str


# ----------------------------------------------------------------- Risk


class RiskAnalysis(StrictModel):
    balance_sheet: str
    debt: str
    dilution: str
    regulatory: str
    execution: str
    geopolitical: str
    valuation_risk: str
    worst_case_scenario: str
    probability_of_permanent_capital_loss: Literal["low", "moderate", "elevated", "high"]
    red_flags: list[str]
    overall_risk: Literal["low", "moderate", "elevated", "high"]
    summary: str


# -------------------------------------------------------------- Options


class LeapsIdea(StrictModel):
    strategy: str
    strike_rationale: str
    expiration_rationale: str
    estimated_max_loss: str
    probability_of_success_view: str
    greeks_notes: str


class OptionsAnalysis(StrictModel):
    leaps_appropriate: bool
    stock_vs_leaps: str
    rationale: str
    leaps_idea: Optional[LeapsIdea]
    liquidity_notes: str
    summary: str


# ------------------------------------------------------------ Portfolio


class HoldingVerdict(StrictModel):
    ticker: str
    verdict: Literal["keep", "trim", "sell", "increase", "replace"]
    rationale: str


class PortfolioReview(StrictModel):
    holdings: list[HoldingVerdict]
    candidate_fit: str
    overlap_concerns: str
    sector_exposure_notes: str
    replacement_suggestion: str
    cash_deployment_note: str


# ----------------------------------------------------- Chair / verdict


class ConvictionDeduction(StrictModel):
    reason: str
    points: float


class ConvictionScore(StrictModel):
    growth: float  # 0-30
    estimate_revisions: float  # 0-25
    profitability: float  # 0-20
    valuation: float  # 0-15
    business_quality: float  # 0-10
    deductions: list[ConvictionDeduction]
    total: float  # sum of components minus deductions


class Scenario(StrictModel):
    narrative: str
    twelve_month_price_view: str
    probability_pct: float


class DecisionJournal(StrictModel):
    why_buy: str
    why_now: str
    why_not_wait: str
    what_could_prove_thesis_wrong: str
    when_to_sell: str
    when_to_average_down: str


class CommitteeVerdict(StrictModel):
    executive_summary: str
    conviction: ConvictionScore
    bull_case: Scenario
    base_case: Scenario
    bear_case: Scenario
    probability_of_outperforming_sp500_pct: float
    confidence: Literal["low", "moderate", "high"]
    expected_upside_pct: float
    expected_downside_pct: float
    decision: Literal["BUY", "HOLD", "SELL", "WATCH", "AVOID"]
    decision_rationale: str
    decision_journal: DecisionJournal
    replacement_analysis: str
    leaps_view: str
    market_risks: list[str]
    key_catalysts: list[str]
    upcoming_earnings: str
