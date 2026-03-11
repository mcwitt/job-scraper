"""HTML report for scraper status."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import jinja2

from job_scraper._report_base import (
    BASE_CSS,
    BASE_JS,
    _epoch,
    _time_ago,
)
from job_scraper.status import SourceStatus

TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
  content="width=device-width, initial-scale=1">
<title>Scraper Status</title>
<style>
{{ base_css }}
  .error { font-size: 0.85em;
    color: var(--badge-red-fg);
    max-width: 300px; overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap; }
  .error:hover { white-space: normal;
    overflow: visible; }
</style>
</head>
<body>
<h1>Scraper Status</h1>
<p class="meta">Generated <time id="generated-at"
  datetime="{{ generated_at }}"></time>
  &middot; {{ total }} sources
  {%- if failing %}, {{ failing }} failing
  {%- endif %}</p>
<div style="display: flex; gap: 1rem;
  align-items: start; flex-wrap: wrap;
  margin-bottom: 1rem;">
  <div class="col-toggle">
    <button class="btn" id="col-btn"
      >Columns &#9662;</button>
    <div class="col-panel" id="col-panel"></div>
  </div>
  <div class="pager" id="pager">
    <button class="btn" id="pg-prev"
      >&lsaquo; Prev</button>
    <span id="pg-info"></span>
    <button class="btn" id="pg-next"
      >Next &rsaquo;</button>
  </div>
</div>
<table>
  <thead>
    <tr>
      <th>Source</th>
      <th data-sort-desc>Last Run</th>
      <th>Status</th>
      <th class="num">Jobs</th>
      <th data-sort-desc>Last Success</th>
      <th class="num">Jobs (success)</th>
      <th>Error</th>
    </tr>
  </thead>
  <tbody>
  {% for name, s in statuses %}
    <tr>
      <td>{{ name }}</td>
      <td data-sort="{{ epoch(s.last_run_at) }}">
        {{- time_ago(s.last_run_at)
          if s.last_run_at
          else '&mdash;' -}}
      </td>
      <td>
        {% if s.last_run_ok is none %}&mdash;
        {% elif s.last_run_ok -%}
          <span class="badge badge-green"
            >OK</span>
        {% else -%}
          <span class="badge badge-red"
            >FAIL</span>
        {%- endif %}
      </td>
      {% set rj = s.last_run_jobs %}
      <td class="num"
        data-sort="{{ rj if rj is not none else -1 }}">
        {{- rj if rj is not none
          else '&mdash;' -}}
      </td>
      <td data-sort="{{ epoch(s.last_success_at) }}">
        {{- time_ago(s.last_success_at)
          if s.last_success_at
          else '&mdash;' -}}
      </td>
      {% set sj = s.last_success_jobs %}
      <td class="num"
        data-sort="{{ sj if sj is not none else -1 }}">
        {{- sj if sj is not none
          else '&mdash;' -}}
      </td>
      <td>{% if s.last_run_error -%}
        <span class="error"
          title="{{ s.last_run_error }}">
          {{- s.last_run_error -}}
        </span>
        {%- else %}&mdash;{% endif %}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
<script>
{{ base_js }}
(function() {
  var showPage = initPager(50);
  initColumns([
    {name: 'Source', on: true},
    {name: 'Last Run', on: true},
    {name: 'Status', on: true},
    {name: 'Jobs', on: true},
    {name: 'Last Success', on: true},
    {name: 'Jobs (success)', on: true},
    {name: 'Error', on: true}
  ], 'scraper-status-cols');
  initSort(function() { showPage(0); });
  showPage(0);
})();
</script>
</body>
</html>
"""


def render_status_report(
    statuses: dict[str, SourceStatus],
    path: Path,
) -> None:
    """Render scraper status as an HTML report."""
    now = datetime.now(UTC)
    sorted_statuses = sorted(
        statuses.items(), key=lambda kv: kv[0]
    )

    env = jinja2.Environment(autoescape=False)  # noqa: S701
    tmpl = env.from_string(TEMPLATE)
    failing = sum(
        1
        for s in statuses.values()
        if s.last_run_ok is False
    )
    html = tmpl.render(
        statuses=sorted_statuses,
        generated_at=now.isoformat(),
        total=len(sorted_statuses),
        failing=failing,
        time_ago=_time_ago,
        epoch=_epoch,
        base_css=BASE_CSS,
        base_js=BASE_JS,
    )
    path.write_text(html)
