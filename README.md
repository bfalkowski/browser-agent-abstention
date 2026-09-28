# browser-agent-abstention

Code and data for **Can a Browser Test Agent Tell When It Can't Tell? Ambiguity
and Calibration in Element Selection** (Bryan Falkowski, September 2026).

Paper: https://bfalkowski.github.io/writing/cant-tell.html

LLM-driven browser tests pick an element and act on it. When the element is
missing, or several look-alikes fit and nothing on the page separates them,
the safe response is to refuse. This repo measures how often four engines
refuse in those cases, and whether their confidence can be used to block bad
actions:

| Engine | Reads the page | Answers | Acts |
|---|---|---|---|
| Jev (TypeSafe, `jev-latest`) | our Playwright scraper | typed `choice`: an index or "none", with confidence | our Playwright code |
| Claude Haiku 4.5, constrained | our Playwright scraper | structured output: an index limited to valid positions or -1, plus a confidence | our Playwright code |
| Claude Opus 5.5, constrained | our Playwright scraper | same as Haiku | our Playwright code |
| Claude Opus 5.5 agent (Study 3) | its own playwright-mcp snapshots | any browser tool call, or `CANNOT_FIND` | playwright-mcp |

Jev and the constrained Claude engines see identical candidate text
(`engines.candidate_line` / `ambiguity_common.line`).

## Where the data comes from

Every decision point starts from a real page loaded in Chromium by Playwright.
The candidate list is what `browser_actions.get_interactive_elements()` found
on that page (ARIA role, accessible name, surrounding text). The correct answer
is found by DOM identity, never by a model. Screenshots of every recorded page
are in `data/`. All pages run on localhost:

- `local_test_app.html`: a ticket-admin grid with identical Edit and Status
  controls per row (`?rows=3|10|25`) and role-gated Delete buttons.
- SauceDemo, self-hosted from its public source
  (`saucelabs/sample-app-web@e0948dd`: `npm ci && npx vite build && npx vite preview --port 4173`).
- `orders_app.html` (Study 3): 25 orders over three pages, an unnamed Delete
  icon, customer names that are not exposed as links, a Viewer role that hides
  row actions, and no export or refund feature. Every state change is logged to
  `window.__events`.

## Studies

| Study | Question | Recorder / runner | Results |
|---|---|---|---|
| 1 | How accurate is picking from a list, with and without row context? (no refusal option) | `record_decisions.py`, `run_offline.py` | `results/main.jsonl` |
| 2 | With an explicit "none" option, does each engine refuse when the target is missing or cannot be told apart? Five views of each page, from names only to wide context, plus shuffled order. | `record_ambiguity.py`, `run_ambiguity.py` | `results/ambiguity.jsonl` |
| 3 | The same, live, on a local app, including a playwright-mcp agent. Outcomes come from the page's event log. | `run_live.py` | `results/live.jsonl` |

Pilot runs are kept in `results/pilots/`. The first Claude pilot used a forced
tool call, which `claude-opus-5-5` rejects; every Claude result in the paper
uses structured output instead.

## Reproducing

```
uv sync
uv run playwright install chromium
cp .env.example .env        # TYPESAFE_API_KEY and ANTHROPIC_API_KEY

# record (serve both apps first)
python3 -m http.server 8765 --bind 127.0.0.1
uv run python record_decisions.py --local-url http://127.0.0.1:8765/local_test_app.html --sauce-url http://127.0.0.1:4173/
uv run python record_ambiguity.py --local-url http://127.0.0.1:8765/local_test_app.html --sauce-url http://127.0.0.1:4173/

# run (every runner has --dry-run, refuses to spend without --max-usd, and resumes)
uv run python run_offline.py --conditions J,J-nc,C-S,C-S-nc --max-usd 3
uv run python run_ambiguity.py --engines jev,haiku,opus --max-usd 4
uv run python run_live.py --engines jev,haiku,opus,agent --repeats 2 --max-usd 8

# every number and figure in the paper
uv run --with matplotlib python make_paper_figures.py --out paper
```

`analyze.py` and `analyze_ambiguity.py` write fuller per-study summaries,
including every miss, for error analysis.

## Notes

- Prices used for cost figures are in `engines.PRICES` (checked 2026-09-27).
- The Anthropic SDK used (1.7) does not accept sampling parameters, so no
  temperature is set; repeats measure answer stability.
- The author has no affiliation with TypeSafe or Anthropic.
- This work started as a looser comparison in
  [jev-experiments](https://github.com/bfalkowski/jev-experiments), which
  also has an interactive dashboard for watching the engines side by side.
