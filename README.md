# AI Investment Committee

A multi-agent investment **research** pipeline built on the Anthropic API
(Claude Opus 4.8). Five specialist agents analyze a stock in parallel using
live market data tools, and a Chair synthesizes their reports into a
conviction-scored verdict, scenario analysis, and decision journal.

> **Not financial advice.** This is decision-support software. It is not a
> licensed advisor, its data (Yahoo Finance) can be delayed or wrong, and
> every investment decision is yours alone.

## Architecture

```
                       ┌────────────────────┐
        ┌─────────────▶│  Quant Analyst      │──┐
        │              ├────────────────────┤  │
 ticker ├─────────────▶│  Business Analyst   │──┤   structured JSON
        │  (parallel)  ├────────────────────┤  ├──────────────┐
        ├─────────────▶│  Risk Manager       │──┤              ▼
        │              ├────────────────────┤  │   ┌──────────────────┐
        └─────────────▶│  Options Specialist │──┘   │ Portfolio Manager │ (optional,
                       └────────────────────┘      └────────┬─────────┘  needs
                                                             ▼            portfolio.json)
                                                   ┌──────────────────┐
                                                   │      Chair        │
                                                   └────────┬─────────┘
                                                            ▼
                                          markdown report + decision journal
```

- **Agents** (`committee/agents.py`) — role prompts encoding a GARP
  philosophy: growth at a reasonable price, earnings acceleration, positive
  estimate revisions, AI infra / semis / software / cloud / fintech focus.
- **Market data tools** (`committee/market_data.py`) — eight yfinance-backed
  tools (fundamentals, analyst estimates & revisions, price history,
  LEAPS option chains, news, earnings, insider activity) exposed to the
  agents through the Anthropic SDK's beta tool runner (`@beta_async_tool`).
- **Structured outputs** (`committee/schemas.py`) — every agent returns a
  Pydantic-validated JSON object (`output_config.format` for tool-using
  agents; `client.messages.parse()` for the Chair).
- **Orchestrator** (`committee/orchestrator.py`) — runs the four analysts
  concurrently with `asyncio.gather`, then the portfolio manager, then the
  Chair. One failed agent degrades the report instead of killing the run.
- **Conviction score** — Chair scores growth /30, estimate revisions /25,
  profitability /20, valuation /15, business quality /10, minus explicit
  deductions (dilution, weak balance sheet, concentration, execution,
  political risk). BUY requires ≥90 plus improving revisions and acceptable
  risk; the rules are encoded in the Chair's system prompt.

## Setup

```bash
cd AIInvestmentComittee
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Credentials: set `ANTHROPIC_API_KEY`, or run `ant auth login` — the SDK picks
up either automatically.

Optional: enable the Portfolio Manager by describing your holdings:

```bash
cp data/portfolio.example.json data/portfolio.json   # then edit
```

## Usage

### Web dashboard

```bash
python -m committee serve          # http://127.0.0.1:8787
```

A local dashboard (FastAPI + vanilla JS, no build step) where you can launch
committee runs, watch agent progress live, browse rendered reports, view
the decision journal, and manage your portfolio. Binds to localhost only.
Start it from a shell where your Anthropic credentials are available.

Dashboard features:

- **Portfolio screen** — every holding with its latest Portfolio Manager
  verdict (keep/trim/sell/increase/replace), filterable by verdict, joined
  with Seeking Alpha quant ratings where available.
- **Seeking Alpha uploads** — SA has no API, so export a screener to xlsx
  and upload it in the sidebar. Files are parsed header-driven (any screen's
  column set works) and merged into `data/seeking_alpha/store.json` with
  latest-upload-wins per ticker. Agents read it through a
  `get_seeking_alpha_data` tool, weigh SA quant ratings and factor grades
  alongside live yfinance data, and report disagreements between the two
  sources in the report's "Data source mismatches" section. Toggle
  "Use Seeking Alpha data" per run (CLI: `--no-seeking-alpha`).

### CLI

```bash
# Full committee run on one or more tickers
python -m committee analyze NVDA
python -m committee analyze NVDA CRWD SOFI

# Skip the portfolio manager
python -m committee analyze NVDA --no-portfolio

# Review past runs
python -m committee journal
python -m committee journal NVDA
```

Each run writes:

- `reports/<TICKER>_<date>.md` — the full committee report
- `data/journal.jsonl` — append-only decision journal (decision, conviction,
  the six journal answers, report path)

**Cost & time:** one ticker ≈ 6 Claude Opus 4.8 calls with multi-step tool
use — typically a few minutes and a few dollars of API usage. Lower
`EFFORT` in `committee/config.py` (e.g. `"medium"`) to trade depth for cost.

## Extending

- **Data sources** — `market_data.py` is the only file that knows about
  yfinance. Swap in or add paid sources (e.g. your own exports) by adding
  more `@beta_async_tool` functions to `MARKET_TOOLS`.
- **Watchlist / weekly report** — `analyze` already accepts multiple
  tickers; a scheduled weekly run over a watchlist file is a natural next
  step (see `data/journal.jsonl` for week-over-week deltas).
- **Rules tuning** — conviction weights, buy/sell thresholds, and portfolio
  constraints live in the prompts in `committee/agents.py`.
