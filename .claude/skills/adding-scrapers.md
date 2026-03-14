# Adding Scrapers

Scrapers are auto-discovered from `job_scraper/scraper/`. Any `.py` file whose name does not start with `_` is loaded as a scraper.

## ATS board scraper (3 lines)

Create `job_scraper/scraper/<ats>_<company>.py`:

```python
from job_scraper.scraper._<ats> import scrape_board

scrape = scrape_board("<company>")
```

Supported ATS helpers: `_greenhouse`, `_ashby`, `_lever`.

## Ad-hoc scraper

Create `job_scraper/scraper/<name>.py` with:

```python
from collections.abc import AsyncIterator
from job_scraper.models import Job
from job_scraper.scraper import GetFn

async def scrape(get: GetFn) -> AsyncIterator[Job]:
    # Custom scraping logic here
    ...
```

No other changes needed — the scraper is auto-discovered on next run.

## Testing a new scraper

After creating a scraper, test it in isolation:

```bash
python -m job_scraper.main --scrape-only --only <module_name>
```

The module name is the filename without `.py` (e.g. `coinbase` for `coinbase.py`).
