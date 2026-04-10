# Job Scraper

Scrape job postings from ATS platforms, score them against candidate preferences using Claude, and output ranked results.

## Installation

Requires Python 3.13+.

```bash
# Clone the repository
git clone https://github.com/mcwitt/job-scraper.git
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

Copy and customize the config files (see [Configuration files](#configuration-files) for details):

```bash
cp scrape.example.toml scrape.toml   # scraper config
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
python -m job_scraper.main run --skip-score

# Boolean pre-filter with FTS5 expression
python -m job_scraper.main run --skip-score --keywords 'title:engineer AND location:remote'

# Full pipeline: scrape + filter + surrogate rank + score + report
python -m job_scraper.main run --keywords '(title:engineer OR title:scientist) NOT title:intern' --report

# Use a specific config file (default: scrape.toml)
python -m job_scraper.main run --config path/to/scrape.toml --skip-score

# Customize scoring model and prep generation model
python -m job_scraper.main run --model claude-haiku-4-5 --prep-model claude-sonnet-4-6

# Control active learning: seed size, explore/exploit batch, iterations
python -m job_scraper.main run --init-num-exploit 100 --num-explore 10 --num-exploit 10 --init-learning-iters 5

# Limit concurrent Claude API requests (default: 10)
# Lower this if you're getting rate limited by the Anthropic API
python -m job_scraper.main run --max-concurrent-api 5

# Run only specific scrapers (comma-separated slugs from config)
python -m job_scraper.main run --scrape-only --only anthropic,openai

# Run all scrapers except specific ones
python -m job_scraper.main run --exclude salesforce,crowdstrike

# Score specific jobs manually (by keywords or hash)
python -m job_scraper.main score --keywords "company:stripe" --report
python -m job_scraper.main score --hash abc123 --hash def456
```

Output goes to `data/output` by default:
- `jobs_raw.jsonl` — all scraped jobs before filtering
- `jobs_surrogate.jsonl` — all filtered jobs with surrogate scores
- `jobs.jsonl` — final ranked output
- `report.html` — interactive HTML report (with `--report`)

## Architecture

**Pipeline:** scrape → dedupe → keywords boolean filter → prep generation → active learning (similarity seed / ensemble disagreement exploration) → surrogate ranking → LLM score top-k → sort → output

### Scraper configuration

Scrapers are configured in `scrape.toml`. There are two types:

**Board scrapers** use built-in support for common ATS platforms (Greenhouse, Ashby, Lever, Gem, Workable, Workday, iCIMS, Phenom, Rippling, SmartRecruiters, Trakstar, Breezy):

```toml
[boards.greenhouse]
anthropic = "Anthropic"

[boards.ashby]
openai = "OpenAI"

[boards.workday]
nvidia = { name = "NVIDIA", instance = "wd5", site = "NVIDIAExternalCareerSite" }
```

**Custom scrapers** are Python scripts or external commands:

```toml
# Python script (must export: async def scrape(http: Http) -> AsyncIterator[Job])
[custom.mycompany]
path = "scrapers/mycompany.py"

# Subprocess (must emit Job JSONL to stdout)
[custom.mycompany]
command = ["python", "scrapers/mycompany.py"]
```

Any entry (board or custom) can include `cache_ttl` (seconds) to override the global `--scrape-ttl` for that scraper:

```toml
[boards.greenhouse]
mycompany = { name = "My Company", cache_ttl = 3600 }
```

See `scrape.example.toml` for the full config format.

### Key modules

| Module | Purpose |
|--------|---------|
| `job_scraper/main.py` | CLI (Typer) and pipeline orchestration |
| `job_scraper/config.py` | Config loader (`scrape.toml`) |
| `job_scraper/relevance.py` | FTS5 boolean filtering against `keywords` |
| `job_scraper/surrogate.py` | TF-IDF + Ridge surrogate with bootstrap ensemble for active learning |
| `job_scraper/prep.py` | Pre-generated interest rubric and candidate brief |
| `job_scraper/scorer.py` | Claude scoring with prompt caching and structured output |
| `job_scraper/llm.py` | Cached Claude API wrapper (`create()` with cache-through) |
| `job_scraper/cache.py` | JSONL append-log cache with TTL |
| `job_scraper/models.py` | `Job` / `ScoredJob` frozen dataclasses |
| `job_scraper/report.py` | Interactive HTML report (Jinja2) |
| `job_scraper/scraper/` | Scraper loader and ATS platform factories |

## Adding scrapers

To scrape a company's job board, find their careers URL and identify the ATS platform from the URL pattern (e.g. `boards.greenhouse.io/SLUG`). Then add an entry to `scrape.toml`:

```toml
[boards.greenhouse]
mycompany = "My Company"
```

For custom scrapers that don't use a supported ATS, create a Python script:

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

Then reference it in your config:

```toml
[custom.mycompany]
path = "scrapers/mycompany.py"
```

### Company context

To improve scoring accuracy, add company context files in the `--companies-dir` directory (default `companies/`). Each file should be named `<canonical-name>.md` where the canonical name is lowercase with non-alphanumeric characters replaced by hyphens.

See `scrape.example.toml` for details.

## Configuration files

All personal config files are gitignored. Copy from `*.example.*` to get started.

### `scrape.toml` — scraper config

Defines which job boards to scrape and any custom scrapers. See `scrape.example.toml`.

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
    job-scraper.url = "github:mcwitt/job-scraper";
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
            companiesDir = ./companies;

            scrape = {
              boards.greenhouse = {
                anthropic = "Anthropic";
              };
              boards.ashby = {
                openai = "OpenAI";
              };
              boards.workday = {
                nvidia = { name = "NVIDIA"; instance = "wd5"; site = "NVIDIAExternalCareerSite"; };
              };
              custom.mycompany = {
                path = ./scrapers/mycompany.py;
              };
            };

            settings = {
              model = "claude-haiku-4-5";
              prepModel = "claude-sonnet-4-6";
              initLearningIters = 5;
            };

            users.alice = {
              preferences = builtins.readFile ./alice/preferences.md;
              resume = builtins.readFile ./alice/resume.md;
              keywords = builtins.readFile ./alice/keywords;
              linkedinConnectionsDir = ./alice/linkedin;
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
- Per-user scoring/reporting
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
