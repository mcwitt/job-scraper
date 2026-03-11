# Job Scraper

Scrape job postings from ATS platforms, score them against a candidate profile using Claude, and output ranked results.

## Setup

Requires [Nix](https://nixos.org/) with flakes enabled.

```bash
# Enter dev shell (or use direnv)
nix develop

# Copy and customize personal config files
cp boards.example.toml boards.toml
cp keywords.example.txt keywords.txt
cp profile.example.md profile.md
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

Scrapers are loaded from two sources:

1. **`boards.toml`** — declarative ATS board definitions (Greenhouse, Ashby, Lever, Gem, Workday). See `boards.example.toml`.
2. **Python modules** in `job_scraper/scraper/` — any `.py` file whose name does not start with `_` is auto-discovered. Must export `async def scrape(http: Http) -> AsyncIterator[Job]`.

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

**ATS board** (Greenhouse, Ashby, Lever, Gem, Workday): add an entry to `boards.toml`:

```toml
[[greenhouse]]
board = "mycompany"
name = "My Company"
```

**Custom scraper**: create `job_scraper/scraper/mycompany.py`:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from job_scraper.hash import job_hash
from job_scraper.models import Job
from job_scraper.scraper._http import Http


async def scrape(http: Http) -> AsyncIterator[Job]:
    now = datetime.now(UTC).isoformat()
    body = await http.get("https://example.com/jobs.json")
    # parse and yield Job objects...
```

## Configuration files

All personal config files are gitignored. Copy from `*.example.*` to get started:

| File | Purpose |
|------|---------|
| `boards.toml` | Which ATS boards to scrape |
| `keywords.txt` | FTS5 query groups for relevance filtering |
| `profile.md` | Free-form candidate profile for interest scoring |
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

            boards = {
              greenhouse = [
                { board = "anthropic"; name = "Anthropic"; }
              ];
              ashby = [
                { board = "openai"; name = "OpenAI"; }
              ];
            };

            settings = {
              model = "claude-haiku-4-5-20251001";
              topK = 100;
            };

            users.alice = {
              profile = builtins.readFile ./alice/profile.md;
              resume = builtins.readFile ./alice/resume.md;
              keywords = builtins.readFile ./alice/keywords.txt;
              linkedinConnectionsDir = ./alice/linkedin;
            };

            users.bob = {
              profile = builtins.readFile ./bob/profile.md;
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
