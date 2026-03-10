# Job Scraper

Scrape job postings from ATS platforms, score them against a candidate profile using Claude, and output ranked results.

## Quick start

```bash
# Scrape all discovered sources + FTS5 relevance filter only (no LLM scoring)
python -m job_scraper.main --skip-score

# Full pipeline (requires ANTHROPIC_API_KEY)
python -m job_scraper.main
```

## Architecture

**Pipeline:** scrape → FTS5 relevance filter → dedupe → LLM score → sort → output

Key files:
- `job_scraper/main.py` — CLI (Typer) and pipeline orchestration
- `job_scraper/relevance.py` — FTS5 relevance scoring against keywords.txt
- `job_scraper/scorer.py` — Claude scoring with extended thinking and structured output
- `job_scraper/cache.py` — JSONL append-log cache with TTL
- `job_scraper/models.py` — Job/ScoredJob frozen dataclasses
- `job_scraper/report.py` — HTML report (Jinja2)
- `job_scraper/scraper/__init__.py` — `ScrapeFn` type, `discover()` auto-discovery
- `job_scraper/scraper/http.py` — cached HTTP GET/POST with rate-limiting
- `job_scraper/scraper/html.py` — shared HTML-to-text utility
- `job_scraper/scraper/greenhouse.py` — Greenhouse `scrape_board()` factory
- `job_scraper/scraper/ashby.py` — Ashby `scrape_board()` factory
- `job_scraper/scraper/lever.py` — Lever `scrape_board()` factory
- `job_scraper/scraper/gem.py` — Gem `scrape_board()` factory
- `job_scraper/scraper/workday.py` — Workday `scrape_board()` factory
- `boards.toml` — ATS board definitions; copy from `boards.example.toml`
- `keywords.txt` — FTS5 query groups (`"phrases"`, `AND`/`OR`/`NOT`, `---` group separators); copy from `keywords.example.txt`
- `profile.md` — candidate profile for LLM scoring; copy from `profile.example.md`
- `resume.md` — candidate resume for recruiter scoring; copy from `resume.example.md`

## Adding scrapers

Two ways to add scrapers:

- **ATS board**: add an entry to `boards.toml` (see `boards.example.toml`)
- **Ad-hoc**: drop a `.py` file in `job_scraper/scraper/` (name must not start with `_`), implement `async def scrape(http: Http) -> AsyncIterator[Job]`

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
