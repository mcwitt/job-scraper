# Job Scraper

Scrape job postings from ATS platforms, score them against a candidate profile using Claude, and output ranked results.

## Quick start

```bash
# Scrape + BM25 relevance filter only (no LLM scoring)
python -m job_scraper.main anthropic deepmind --skip-score

# Full pipeline (requires ANTHROPIC_API_KEY)
python -m job_scraper.main anthropic deepmind
```

## Architecture

**Pipeline:** scrape → BM25 relevance filter → dedupe → LLM score → sort → output

Key files:
- `job_scraper/main.py` — CLI (Typer) and pipeline orchestration
- `job_scraper/relevance.py` — BM25 relevance scoring against keywords.txt
- `job_scraper/scorer.py` — Claude scoring with extended thinking and structured output
- `job_scraper/cache.py` — JSONL append-log cache with TTL
- `job_scraper/models.py` — Job/ScoredJob frozen dataclasses
- `job_scraper/report.py` — HTML report (Jinja2)
- `job_scraper/scraper/greenhouse.py` — Greenhouse boards API scraper
- `job_scraper/scraper/http.py` — cached HTTP GET closure
- `keywords.txt` — BM25 query terms (one per line, `#` comments)
- `profile.md` — candidate profile for LLM scoring

## Style

- Functional style: closures for state, no ABCs or inheritance
- Frozen dataclasses for data
- Ruff line length: 88 chars
- No unnecessary abstractions or over-engineering

## Dev environment

Nix flake with direnv. `nix develop` or `direnv allow` to enter the shell.

Pre-commit hooks (run via `nix fmt` or `pre-commit run --all-files`):
- nixfmt
- pyrefly (type checker)
- ruff (linter)

## Scores

LLM scores are floats 0.0-1.0 internally. Only the HTML report converts to 0-100 for display.
