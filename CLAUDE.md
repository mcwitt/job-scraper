# Job Scraper

Scrape job postings from ATS platforms, score them against candidate preferences using Claude, and output ranked results.

## Quick start

```bash
# Scrape all configured sources, skip scoring
python -m job_scraper.main run --skip-score

# Full pipeline (requires ANTHROPIC_API_KEY)
python -m job_scraper.main run

# Use a specific config file
python -m job_scraper.main run --config path/to/scrape.toml

# Score specific jobs manually (merges into existing output)
python -m job_scraper.main score --keywords "company:stripe" --report
python -m job_scraper.main score --keywords "company:stripe title:engineer"
python -m job_scraper.main score --hash abc123
```

## Architecture

**Pipeline:** scrape → upsert/evict store → dedupe → keywords boolean filter → prep (interest rubric + candidate brief) → active learning (similarity seed + explore/exploit loop) → surrogate ranking → sort → output

Key files:
- `job_scraper/main.py` — CLI (Typer) and pipeline orchestration
- `job_scraper/config.py` — TOML config loader (`scrape.toml`)
- `job_scraper/relevance.py` — FTS5 boolean filtering against keywords
- `job_scraper/prep.py` — pre-generated interest rubric and candidate brief
- `job_scraper/surrogate.py` — DualVectorizer TF-IDF + dual-head Ridge surrogate with bootstrap ensemble for active learning
- `job_scraper/scorer.py` — Claude scoring with prompt caching and structured output (single combined call)
- `job_scraper/llm.py` — cached Claude API wrapper (`create()` with cache-through)
- `job_scraper/companies/` — `canonicalize`/`load_companies` (loads `.md` files from configurable directory)
- `job_scraper/cache.py` — JSONL append-log cache with TTL
- `job_scraper/store.py` — persistent per-job store with retention (`data/state/jobs_store.jsonl`); carries forward unobserved jobs for `--retain-for-seconds`
- `job_scraper/models.py` — Job/ScoredJob/Interest/Fit frozen dataclasses
- `job_scraper/report.py` — HTML report (Jinja2)
- `job_scraper/scraper/__init__.py` — `ScrapeFn` type, `load_scrapers()` config-driven loader
- `job_scraper/scraper/http.py` — cached HTTP GET/POST with rate-limiting
- `job_scraper/scraper/html.py` — shared HTML-to-text utility
- `job_scraper/scraper/greenhouse.py` — Greenhouse `scrape_board()` factory
- `job_scraper/scraper/ashby.py` — Ashby `scrape_board()` factory
- `job_scraper/scraper/lever.py` — Lever `scrape_board()` factory
- `job_scraper/scraper/gem.py` — Gem `scrape_board()` factory
- `job_scraper/scraper/workday.py` — Workday `scrape_board()` factory
- `job_scraper/scraper/icims.py` — iCIMS Attract `scrape_board()` factory
- `job_scraper/scraper/phenom.py` — Phenom People `scrape_board()` factory
- `job_scraper/scraper/rippling.py` — Rippling `scrape_board()` factory
- `job_scraper/scraper/smartrecruiters.py` — SmartRecruiters `scrape_board()` factory
- `job_scraper/scraper/trakstar.py` — Trakstar Hire `scrape_board()` factory
- `job_scraper/scraper/workable.py` — Workable `scrape_board()` factory
- `job_scraper/scraper/breezy.py` — Breezy `scrape_board()` factory
- `job_scraper/scraper/kula.py` — Kula `scrape_board()` factory
- `scrape.example.toml` — example scraper config
- `preferences.md` — candidate job preferences for interest scoring; copy from `preferences.example.md`
- `resume.md` — candidate resume for recruiter scoring; copy from `resume.example.md`

## Scraper configuration

Scrapers are configured in `scrape.toml` (default path, override with `--config`). See `scrape.example.toml` for format.

**Board scrapers** use built-in ATS platform support:
```toml
[boards.greenhouse]
mycompany.name = "My Company"
```

**Custom scrapers** are Python scripts implementing `async def scrape(http: Http) -> AsyncIterator[Job]`:
```toml
[custom.mycompany]
path = "scrapers/mycompany.py"
```

Or subprocess commands that emit Job JSONL to stdout:
```toml
[custom.mycompany]
command = ["python", "scrapers/mycompany.py"]
```

To identify a company's ATS platform, follow the "Careers" or "Jobs" link from their website — the job listing URLs reveal the platform (e.g. `boards.greenhouse.io/SLUG`, `jobs.ashbyhq.com/SLUG`, `jobs.lever.co/SLUG`, `ats.rippling.com/SLUG`, `apply.workable.com/SLUG`). Verify the slug works by hitting the platform's API before adding it to config.

### Company context

Company context files improve scoring accuracy. Place `.md` files in the `--companies-dir` directory (default `companies/`). Name files `<canonical-name>.md` (lowercase, non-alphanumeric replaced with hyphens). Include these sections:

- **Overview** — What the company does, when founded, founder(s), HQ location
- **Technical Focus** — Key technology areas and platforms (bulleted)
- **Scale & Stage** — Public/private, funding stage & amount, approximate headcount
- **Hiring** — What roles look like, hiring bar, culture notes
- **Recent Context** — Latest news, partnerships, product launches

Target ~30-45 lines, hard-wrapped at ~65 chars.

## Keeping docs in sync

When changing CLI args, defaults, pipeline stages, or key modules, also update `README.md` and `nix/module.nix` to match.

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

LLM scores are int 0-100 throughout. The surrogate model normalizes to [0, 1] internally via `score / 100`.

## Prep artifacts

Scoring uses pre-generated artifacts instead of raw preferences/resume in each scoring call (cached in `prep.jsonl`):
- **Interest rubric** — generated once from `(meta_prompt, preferences.md)`; includes candidate-specific scoring bands and weights
- **Candidate brief** — generated once from `(meta_prompt, resume.md)`; factual summary of resume for recruiter assessment. Fit scoring structure (dimensions, weights, anchors) is hardcoded in `scorer.py`; company context injected at scoring time.
- `--prep-model` controls the model used for prep generation (default: sonnet)
