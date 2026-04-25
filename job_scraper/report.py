from datetime import UTC, datetime
from pathlib import Path

import mistune
from jinja2 import Environment
from markupsafe import Markup

from job_scraper._report_base import (
    BASE_CSS,
    BASE_JS,
    _epoch,
    _time_ago,
)
from job_scraper.companies import canonicalize, load_companies
from job_scraper.linkedin import (
    Connection,
    LookupFn,
    SecondDegree,
)
from job_scraper.models import ScoredJob
from job_scraper.store import (
    DEFAULT_WARN_AFTER_SECONDS,
    is_stale,
    parse_iso,
)

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
  content="width=device-width, initial-scale=1">
<title>Job Scraper Report</title>
<style>
{{ base_css | safe }}
  .narrow { padding-left: 0.3rem;
    padding-right: 0.3rem; text-align: center;
    white-space: nowrap; }
  .status { padding-left: 0.2rem;
    padding-right: 0.2rem; text-align: center;
    white-space: nowrap; width: 1.5rem; }
  .tip { position: relative; cursor: help; }
  .tip .tip-body { display: none;
    position: absolute; left: 0; top: 100%;
    z-index: 10; background: var(--panel-bg);
    border: 1px solid var(--panel-border);
    border-radius: 6px;
    box-shadow: var(--panel-shadow);
    padding: 0.75rem; max-width: 420px;
    font-weight: normal; font-size: 0.85em;
    color: var(--fg); white-space: normal;
    text-align: left; }
  .tip:hover .tip-body,
  .tip.active .tip-body { display: block; }
  .tip.flip .tip-body { top: auto; bottom: 100%; }
  .tip-score .tip-body { min-width: 320px; }
  .tip-status .tip-body { min-width: 240px; }
  .tip-company { max-width: 300px; }
  .tip-company .tip-body { min-width: 400px;
    max-width: 520px;
    max-height: 400px; overflow-y: auto; }
  .cell-text { display: inline-block;
    max-width: 100%; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
    vertical-align: bottom; }
  .tip-company .tip-body h1,
  .tip-company .tip-body h2 {
    font-size: 0.95em; margin: 0.6rem 0 0.2rem; }
  .tip-company .tip-body h1:first-child,
  .tip-company .tip-body h2:first-child {
    margin-top: 0; }
  .tip-company .tip-body p { margin: 0.3rem 0; }
  .tip-body ul { margin: 0;
    padding-left: 1.2em; }
  .tip-body li { margin: 0.15rem 0; }
  .tip-body dt { font-weight: 600;
    margin-top: 0.4rem; }
  .tip-body dt:first-child { margin-top: 0; }
  .tip-body dd { margin: 0.1rem 0 0 0;
    color: var(--muted); }
  .cell { max-width: 300px; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
    cursor: default; }
  .cell.expanded { white-space: normal;
    overflow: visible; }
  .conns { text-align: center; }
  .date { white-space: nowrap;
    font-size: 0.85em; }
  .search { min-width: 200px;
    cursor: text; }
  .search-hide { display: none !important; }
</style>
</head>
<body>
<h1>Job Scraper Report</h1>
<p class="meta" style="margin-bottom: 1rem;"
  >Generated <time id="generated-at"
    datetime="{{ generated_at }}"></time>
  &middot; {{ jobs | length }} jobs scored</p>
<div style="display: flex; gap: 1rem;
  align-items: start; flex-wrap: wrap;
  margin-bottom: 1rem;">
  <input type="text" id="search" class="btn search"
    placeholder="Filter jobs\u2026"
    autocomplete="off" spellcheck="false">
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
      <th class="narrow"
        title="Geometric mean of Interest and Fit"
        >Score</th>
      <th class="narrow"
        title="Interest to candidate">Interest</th>
      <th class="narrow"
        title="Fit for role">Fit</th>
      <th data-sort-desc>Posted</th>
      <th class="status"
        title="Stale data warnings"></th>
      <th data-search>Title</th>
      <th data-search>Company</th>
      <th title="1st-degree connections">1st</th>
      <th title="2nd-degree connections">2nd</th>
      <th data-search>Team</th>
      <th data-search>Location</th>
      <th data-search>Compensation</th>
      <th>Last seen</th>
    </tr>
  </thead>
  <tbody>
    {% for job in jobs %}
    {% set cv = job.score_interest.score %}
    {% set rv = job.score_fit.score %}
    {% set score = (cv * rv) ** 0.5 %}
    {% set first, second = lookup(job.company) %}
    <tr>
      <td class="narrow tip tip-score"
        data-sort="{{ score }}">
        <span class="badge {{ score_class(score) }}"
          >{{ score | round(0) | int }}</span>
        <div class="tip-body">
          <dt>Interest {{ cv }}</dt>
          <dd>{{ job.score_interest.summary }}</dd>
          <dt>Fit {{ rv }}</dt>
          <dd>{{ job.score_fit.summary }}</dd>
        </div>
      </td>
      <td class="narrow tip tip-score"
        data-sort="{{ cv }}">
        <span class="badge {{ score_class(cv) }}"
          >{{ cv }}</span>
        <div class="tip-body">
          {{ job.score_interest.summary }}</div>
      </td>
      <td class="narrow tip tip-score"
        data-sort="{{ rv }}">
        <span class="badge {{ score_class(rv) }}"
          >{{ rv }}</span>
        <div class="tip-body">
          {{ job.score_fit.summary }}</div>
      </td>
      <td class="date"
        data-sort="{{ epoch(job.posted) }}">
        {% if job.posted %}
        <span class="{{ date_class(job.posted) }}"
          title="{{ job.posted }}">
          {{- time_ago(job.posted) -}}
        </span>{% endif %}</td>
      {% set stale = is_stale(job.last_seen_at) %}
      <td class="status{% if stale %} tip tip-status{% endif %}"
        data-sort="{{ 1 if stale else 0 }}">
        {% if stale %}⚠️
        <div class="tip-body">
          Last observed {{ time_ago(job.last_seen_at) }}.
          Showing cached record.
        </div>{% endif %}</td>
      <td class="cell">
        <a href="{{ job.url }}">
          {{- job.title -}}
        </a></td>
      {% set ctx = company_ctx.get(
        job.company) %}
      {% if ctx %}
      <td class="tip tip-company">
        <span class="cell-text"
          >{{ job.company }}</span>
        <div class="tip-body">{{ ctx }}</div>
      </td>
      {% else %}
      <td class="cell">{{ job.company }}</td>
      {% endif %}
      {% set n1 = first | length %}
      <td class="conns tip"
        data-sort="{{ n1 }}">
        {{- n1 or "" -}}
        {%- if first %}
        <div class="tip-body">
        <ul>
        {%- for c in first %}
          <li><a href="{{ c.url }}"
            >{{ c.name }}</a></li>
        {%- endfor %}
        </ul>
        </div>
        {%- endif %}
      </td>
      {% set n2 = second
        | map(attribute='connections')
        | map('length') | sum %}
      <td class="conns tip"
        data-sort="{{ n2 }}">
        {{- n2 or "" -}}
        {%- if second %}
        <div class="tip-body">
        <ul>
        {%- for g in second %}
          <li><a href="{{ g.via.url }}"
            >{{ g.via.name }}</a>
            <ul>
            {%- for c in g.connections %}
              <li><a href="{{ c.url }}"
                >{{ c.name }}</a></li>
            {%- endfor %}
            </ul>
          </li>
        {%- endfor %}
        </ul>
        </div>
        {%- endif %}
      </td>
      <td class="cell">{{ job.team or "" }}</td>
      <td class="cell">
        {{ job.location or "" }}</td>
      <td class="cell">{{ job.comp or "" }}</td>
      <td class="date"
        data-sort="{{ epoch(job.last_seen_at) }}"
        title="{{ job.last_seen_at }}">
        {{ time_ago(job.last_seen_at) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<script>
{{ base_js | safe }}
document.querySelectorAll('.cell')
  .forEach(function(el) {
    el.addEventListener('click', function() {
      this.classList.toggle('expanded');
    });
  });
function positionTip(el) {
  el.classList.remove('flip');
  var body = el.querySelector('.tip-body');
  if (!body) return;
  body.style.display = 'block';
  void body.offsetHeight;
  var rect = body.getBoundingClientRect();
  body.style.display = '';
  var vh = window.visualViewport
    ? window.visualViewport.height
    : window.innerHeight;
  if (rect.bottom > vh) {
    el.classList.add('flip');
  }
}
document.querySelectorAll('.tip')
  .forEach(function(el) {
    el.addEventListener('mouseenter', function() {
      positionTip(el);
    });
    el.addEventListener('click', function(e) {
      document.querySelectorAll('.tip.active')
        .forEach(function(t) {
          if (t !== el)
            t.classList.remove('active');
        });
      this.classList.toggle('active');
      if (el.classList.contains('active')) positionTip(el);
      e.stopPropagation();
    });
  });
document.addEventListener('click', function() {
  document.querySelectorAll('.tip.active')
    .forEach(function(t) {
      t.classList.remove('active');
    });
});
(function() {
  var showPage = initPager(50);
  initSearch(function() { showPage(0); });
  initColumns([
    {name: 'Score', on: true},
    {name: 'Interest', on: false},
    {name: 'Fit', on: false},
    {name: 'Posted', on: true},
    {name: 'Status', on: true},
    {name: 'Title', on: true},
    {name: 'Company', on: true},
    {name: '1st', on: true},
    {name: '2nd', on: true},
    {name: 'Team', on: false},
    {name: 'Location', on: false},
    {name: 'Compensation', on: false},
    {name: 'Last seen', on: false}
  ], 'job-scraper-cols');
  initSort(function() { showPage(0); });
  showPage(0);
})();
</script>
</body>
</html>
"""


def _date_class(date_str: str | None) -> str:
    if not date_str:
        return ""
    try:
        dt = parse_iso(date_str)
    except ValueError:
        return ""
    days = (datetime.now(UTC) - dt).days
    if days < 7:
        return "badge badge-green"
    return ""


def _score_class(score: float) -> str:
    if score >= 70:
        return "badge-green"
    if score >= 40:
        return "badge-yellow"
    return "badge-red"


def _no_connections(
    company: str,
) -> tuple[list[Connection], list[SecondDegree]]:
    return [], []


def _build_company_ctx(
    jobs: list[ScoredJob],
    companies_dir: Path,
) -> dict[str, Markup]:
    canonical = load_companies(companies_dir)
    md = mistune.create_markdown()
    ctx: dict[str, Markup] = {}
    for job in jobs:
        name = job.company
        if name in ctx:
            continue
        content = canonical.get(canonicalize(name))
        if content:
            ctx[name] = Markup(md(content))  # noqa: S704
    return ctx


def render_report(
    jobs: list[ScoredJob],
    path: Path,
    lookup: LookupFn | None = None,
    companies_dir: Path = Path("companies"),
    warn_after_seconds: int = DEFAULT_WARN_AFTER_SECONDS,
) -> None:
    env = Environment(autoescape=True)
    template = env.from_string(TEMPLATE)
    now_dt = datetime.now(UTC)
    html = template.render(
        jobs=jobs,
        generated_at=now_dt.isoformat(),
        score_class=_score_class,
        date_class=_date_class,
        time_ago=_time_ago,
        epoch=_epoch,
        lookup=lookup or _no_connections,
        company_ctx=_build_company_ctx(jobs, companies_dir),
        is_stale=lambda ts: is_stale(ts, now_dt, warn_after_seconds),
        base_css=BASE_CSS,
        base_js=BASE_JS,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
