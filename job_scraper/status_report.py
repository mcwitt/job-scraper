"""HTML report for scraper status."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import jinja2

from job_scraper.report import _time_ago
from job_scraper.status import SourceStatus

TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scraper Status</title>
<style>
  :root { --bg: #fff; --fg: #111; --border: #ddd; --ok: #2e7d32;
          --fail: #c62828; --muted: #888; --stripe: #f9f9f9;
          --hover: #f0f4ff; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #1a1a1a; --fg: #e0e0e0; --border: #333;
            --ok: #66bb6a; --fail: #ef5350; --muted: #999;
            --stripe: #222; --hover: #2a2a3a; }
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: var(--bg);
         color: var(--fg); padding: 2rem; max-width: 1100px; margin: auto; }
  h1 { margin-bottom: .3rem; font-size: 1.5rem; }
  .meta { color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; }
  table { width: 100%; border-collapse: collapse; font-size: .9rem; }
  th, td { padding: .55rem .75rem; text-align: left;
           border-bottom: 1px solid var(--border); }
  th { font-weight: 600; font-size: .8rem; text-transform: uppercase;
       letter-spacing: .03em; color: var(--muted); }
  tr:nth-child(even) td { background: var(--stripe); }
  tr:hover td { background: var(--hover); }
  .ok { color: var(--ok); font-weight: 600; }
  .fail { color: var(--fail); font-weight: 600; }
  .none { color: var(--muted); }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .error { font-size: .8rem; color: var(--fail); max-width: 300px;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .error:hover { white-space: normal; overflow: visible; }
</style>
</head>
<body>
<h1>Scraper Status</h1>
<p class="meta">Generated {{ generated_at }}
  &mdash; {{ total }} sources</p>
<table>
  <thead>
    <tr>
      <th>Source</th>
      <th>Last Run</th>
      <th>Status</th>
      <th class="num">Jobs</th>
      <th>Last Success</th>
      <th class="num">Jobs (success)</th>
      <th>Error</th>
    </tr>
  </thead>
  <tbody>
  {% for name, s in statuses %}
    <tr>
      <td>{{ name }}</td>
      <td>{{ time_ago(s.last_run_at) if s.last_run_at else '&mdash;' }}</td>
      <td>
        {% if s.last_run_ok is none %}<span class="none">&mdash;</span>
        {% elif s.last_run_ok %}<span class="ok">OK</span>
        {% else %}<span class="fail">FAIL</span>{% endif %}
      </td>
      <td class="num">
        {{- s.last_run_jobs if s.last_run_jobs is not none else '&mdash;' -}}
      </td>
      <td>
        {{- time_ago(s.last_success_at) if s.last_success_at else '&mdash;' -}}
      </td>
      <td class="num">
        {{- s.last_success_jobs if s.last_success_jobs is not none else '&mdash;' -}}
      </td>
      <td>{% if s.last_run_error %}<span class="error"
        title="{{ s.last_run_error }}">
        {{- s.last_run_error -}}
        </span>{% else %}&mdash;{% endif %}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</body>
</html>
"""


def render_status_report(
    statuses: dict[str, SourceStatus],
    path: Path,
) -> None:
    """Render scraper status as an HTML report."""
    now = datetime.now(UTC)
    sorted_statuses = sorted(statuses.items(), key=lambda kv: kv[0])

    env = jinja2.Environment(autoescape=False)  # noqa: S701
    tmpl = env.from_string(TEMPLATE)
    html = tmpl.render(
        statuses=sorted_statuses,
        generated_at=now.strftime("%Y-%m-%d %H:%M UTC"),
        total=len(sorted_statuses),
        time_ago=_time_ago,
    )
    path.write_text(html)
