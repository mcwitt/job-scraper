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

Copy and customize the personal config files:

```bash
cp keywords.example.txt keywords.txt
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

All personal config files are gitignored. Copy from `*.example.*` to get started.

### `keywords.txt` — relevance prefilter

Used in the **FTS5 relevance filter** step to cheaply discard irrelevant jobs before LLM scoring. This is a SQLite FTS5 query file — jobs that don't match any group are filtered out entirely, so the LLM only sees jobs that passed at least one keyword group.

Syntax: `"phrases"`, `AND`/`OR`/`NOT`, `(grouping)`. Groups are separated by `---`; a job's relevance score is the max across all groups. Prefix terms with `title:` or `description:` to restrict matching to that column.

See `keywords.example.txt` for a full example.

### `preferences.md` — interest scoring

Describes what you're looking for in your next role: target titles, ideal characteristics, dealbreakers, and location constraints. Claude reads this alongside each job posting to produce an **interest score** — how excited you would be about the role based on your stated aspirations and preferences.

### `resume.md` — recruiter-fit scoring

Your resume in Markdown. Claude reads this alongside each job posting to produce a **fit score** — how likely a recruiter would be to advance your application based on your background and experience. This is scored independently from interest so you can see roles you'd love but might be a stretch, and roles you're qualified for but might not want.

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
