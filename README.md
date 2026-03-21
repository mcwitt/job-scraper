# Job Scraper

Scrape job postings from ATS platforms, score them against candidate preferences using Claude, and output ranked results.

## Installation

Requires Python 3.13+.

```bash
# Clone the repository
git clone https://github.com/you/job-scraper.git
cd job-scraper

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install the package
pip install .
```

For development (editable install):

```bash
pip install -e .
```

### Configuration

Copy and customize the personal config files (see [Configuration files](#configuration-files) for details):

```bash
cp preferences.example.md preferences.md
cp resume.example.md resume.md
```

Set `ANTHROPIC_API_KEY` in your environment for LLM scoring:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### Nix

Alternatively, if you use [Nix](https://nixos.org/) with flakes:

```bash
nix develop    # or use direnv
```

## Usage

```bash
# Scrape only (no filtering or scoring)
python -m job_scraper.main --skip-score

# Boolean pre-filter with FTS5 expression
python -m job_scraper.main --skip-score --keywords 'title:engineer AND location:remote'

# Full pipeline: scrape + filter + surrogate rank + score + report
python -m job_scraper.main --keywords '(title:engineer OR title:scientist) NOT title:intern' --report

# Customize scoring model and prep generation model
python -m job_scraper.main --model claude-haiku-4-5-20251001 --prep-model claude-sonnet-4-6

# Keep only the top 50 jobs for LLM scoring (default: 200)
python -m job_scraper.main --top-k 50

# Control active learning: seed size, exploration budget, iterations
python -m job_scraper.main --num-cold-start 100 --num-explore 10 --num-active-iters 2

# Limit concurrent Claude API requests (default: 10)
# Lower this if you're getting rate limited by the Anthropic API
python -m job_scraper.main --max-concurrent-api 5

# Run only specific scrapers (comma-separated module names)
python -m job_scraper.main --scrape-only --only discord,figma,linear

# Run all scrapers except specific ones
python -m job_scraper.main --exclude salesforce,crowdstrike
```

Output goes to `data/output/` by default:
- `jobs_raw.jsonl` — all scraped jobs before filtering
- `jobs_surrogate.jsonl` — all filtered jobs with surrogate scores
- `jobs.jsonl` — final ranked output
- `report.html` — interactive HTML report (with `--report`)

## Architecture

**Pipeline:** scrape → dedupe → keywords boolean filter → prep generation → active learning (similarity seed / ensemble disagreement exploration) → surrogate ranking → LLM score top-k → sort → output

### Scraper discovery

Every `.py` file in `job_scraper/scraper/` whose name does not start with `_` is auto-discovered. Each must export `async def scrape(http: Http) -> AsyncIterator[Job]`.

### Key modules

| Module | Purpose |
|--------|---------|
| `job_scraper/main.py` | CLI (Typer) and pipeline orchestration |
| `job_scraper/relevance.py` | FTS5 boolean filtering against `keywords` |
| `job_scraper/surrogate.py` | TF-IDF + Ridge surrogate with bootstrap ensemble for active learning |
| `job_scraper/prep.py` | Pre-generated interest rubric and candidate brief |
| `job_scraper/scorer.py` | Claude scoring with prompt caching and structured output |
| `job_scraper/cache.py` | JSONL append-log cache with TTL |
| `job_scraper/models.py` | `Job` / `ScoredJob` frozen dataclasses |
| `job_scraper/report.py` | Interactive HTML report (Jinja2) |
| `job_scraper/scraper/` | Scraper factories and ad-hoc scrapers |

## Adding scrapers

**ATS board** (Greenhouse, Ashby, Lever, Gem, Workable, Workday): create a stub in `job_scraper/scraper/`:

```python
from job_scraper.scraper.greenhouse import scrape_board

scrape = scrape_board("mycompany", name="My Company")
```

**Custom scraper**: create `job_scraper/scraper/mycompany.py`:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper.http import Http


async def scrape(http: Http) -> AsyncIterator[Job]:
    now = datetime.now(UTC).isoformat()
    resp = await http.get("https://example.com/jobs.json")
    # parse resp.body and yield Job objects...
```

## Configuration files

All personal config files are gitignored. Copy from `*.example.*` to get started.

### `--keywords` — boolean prefilter

Pass a [SQLite FTS5 query expression](https://www.sqlite.org/fts5.html#full_text_query_syntax) via `--keywords` to cheaply discard irrelevant jobs before surrogate ranking and LLM scoring. Jobs that don't match are filtered out entirely. When omitted, no filtering is applied.

Syntax: `"phrases"`, `AND`/`OR`/`NOT`, `(grouping)`. Prefix terms with `title:` or `description:` to restrict matching to that column.

### `preferences.md` — interest scoring

Describes what you're looking for in your next role: target titles, ideal characteristics, hard constraints, and location preferences. The prep model reads this once to produce a candidate-specific interest rubric with concrete scoring bands. The rubric is then used to score each job consistently.

### `resume.md` — recruiter-fit scoring

Your resume in Markdown. The prep model reads this once to produce a concise candidate brief — a factual distillation of your resume for recruiter assessment. The brief is then used alongside company context and hardcoded scoring dimensions to produce a **fit score** — how likely a recruiter would advance your application. This is scored independently from interest so you can see roles you'd love but might be a stretch, and roles you're qualified for but might not want.

## NixOS deployment

Add the flake as an input and import the module:

```nix
{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    job-scraper.url = "github:you/job-scraper";
  };

  outputs = { nixpkgs, job-scraper, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        job-scraper.nixosModules.default
        {
          services.job-scraper = {
            enable = true;
            schedule = "daily";
            anthropicApiKeyFile = "/run/secrets/anthropic-api-key";

            settings = {
              model = "claude-haiku-4-5";
              prepModel = "claude-sonnet-4-6";
              topK = 200;
              numActiveIters = 3;  # active learning iterations on cold start
            };

            users.alice = {
              preferences = builtins.readFile ./alice/preferences.md;
              resume = builtins.readFile ./alice/resume.md;
              keywords = builtins.readFile ./alice/keywords;
              linkedinConnectionsDir = ./alice/linkedin;
            };

            users.bob = {
              preferences = builtins.readFile ./bob/preferences.md;
              resume = builtins.readFile ./bob/resume.md;
              keywords = builtins.readFile ./bob/keywords;
            };
          };
        }
      ];
    };
  };
}
```

This sets up:
- A shared scrape phase running daily via systemd timer
- Per-user scoring/reporting (Alice and Bob scored in parallel)
- Reports written to each user's `outputDir` (e.g. `/var/lib/job-scraper/users/<id>/output/`)

Trigger manually with `systemctl start job-scraper.service`.

## Development

```bash
pip install -e .

# Lint and type-check
ruff check .
pyrefly check
```

With Nix:

```bash
nix develop        # enter dev shell
nix fmt            # run all formatters/linters
```

Pre-commit hooks: nixfmt, pyrefly (type checker), ruff (linter).
