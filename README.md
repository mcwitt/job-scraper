# Job Scraper

Scrape job postings from ATS platforms, score them against candidate preferences using Claude, and output ranked results.

## Setup

Requires [Nix](https://nixos.org/) with flakes enabled.

```bash
# Enter dev shell (or use direnv)
nix develop

# Copy and customize personal config files
cp keywords.example.txt keywords.txt
cp preferences.example.md preferences.md
cp resume.example.md resume.md
```

Set `ANTHROPIC_API_KEY` in your environment (e.g. in `.envrc`) for LLM scoring.

## Usage

```bash
# Scrape + FTS5 relevance filter only (no LLM scoring)
python -m job_scraper.main --skip-score

# Full pipeline: scrape + filter + score + report
python -m job_scraper.main --report

# Customize scoring model and batch size
python -m job_scraper.main --model claude-haiku-4-5-20251001 --batch-size 20
```

Output goes to `data/output/` by default:
- `jobs_raw.jsonl` — all scraped jobs before filtering
- `jobs_relevance.jsonl` — all jobs with FTS5 relevance scores
- `jobs.jsonl` — final ranked output
- `report.html` — interactive HTML report (with `--report`)

## Architecture

**Pipeline:** scrape → FTS5 relevance filter → dedupe → LLM score → sort → output

### Scraper discovery

Every `.py` file in `job_scraper/scraper/` whose name does not start with `_` is auto-discovered. Each must export `async def scrape(http: Http) -> AsyncIterator[Job]`.

### Key modules

| Module | Purpose |
|--------|---------|
| `job_scraper/main.py` | CLI (Typer) and pipeline orchestration |
| `job_scraper/relevance.py` | FTS5 relevance scoring against `keywords.txt` |
| `job_scraper/scorer.py` | Claude scoring with extended thinking |
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
    body = await http.get("https://example.com/jobs.json")
    # parse and yield Job objects...
```

## Configuration files

All personal config files are gitignored. Copy from `*.example.*` to get started:

| File | Purpose |
|------|---------|
| `keywords.txt` | FTS5 query groups for relevance filtering |
| `preferences.md` | What the candidate is looking for in a job |
| `resume.md` | Candidate resume for recruiter-fit scoring |

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
              model = "claude-haiku-4-5-20251001";
              topK = 100;
            };

            users.alice = {
              preferences = builtins.readFile ./alice/preferences.md;
              resume = builtins.readFile ./alice/resume.md;
              keywords = builtins.readFile ./alice/keywords.txt;
              linkedinConnectionsDir = ./alice/linkedin;
            };

            users.bob = {
              preferences = builtins.readFile ./bob/preferences.md;
              resume = builtins.readFile ./bob/resume.md;
              keywords = builtins.readFile ./bob/keywords.txt;
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
nix develop        # enter dev shell
nix fmt            # run all formatters/linters
```

Pre-commit hooks: nixfmt, pyrefly (type checker), ruff (linter).
