from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment

from job_scraper._report_base import (
    BASE_CSS,
    BASE_JS,
    _epoch,
    _time_ago,
)
from job_scraper.linkedin import (
    Connection,
    LookupFn,
    SecondDegree,
)
from job_scraper.models import ScoredJob

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
  .tip-score .tip-body { min-width: 320px; }
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
  .pager { display: flex; align-items: center;
    gap: 0.5rem; margin-bottom: 1rem;
    font-size: 0.85em; }
</style>
</head>
<body>
<h1>Job Scraper Report</h1>
<p class="meta" style="margin-bottom: 1rem;"
  >Generated <time id="generated-at"
    datetime="{{ generated_at }}"></time>
  &middot; {{ jobs | length }} jobs scored</p>
<div style="display: flex; gap: 1rem;
  align-items: start; flex-wrap: wrap;">
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
      <th>Title</th>
      <th>Company</th>
      <th title="1st-degree connections">1st</th>
      <th title="2nd-degree connections">2nd</th>
      <th>Team</th>
      <th>Location</th>
      <th>Compensation</th>
      <th>Scraped</th>
    </tr>
  </thead>
  <tbody>
    {% for job in jobs %}
    {% set cv = job.score_interest.value %}
    {% set rv = job.score_fit.value if job.score_fit else cv %}
    {% set score = (cv * rv) ** 0.5 %}
    {% set first, second = lookup(job.company) %}
    <tr>
      <td class="narrow tip tip-score"
        data-sort="{{ score }}">
        <span class="badge {{ score_class(score) }}"
          >{{ (score * 100) | round(0) | int }}</span>
        <div class="tip-body">
          <dt>Interest
            {{ (cv * 100) | round(0) | int }}</dt>
          <dd>{{ job.score_interest.why }}</dd>
          {% if job.score_fit is not none %}
          <dt>Fit
            {{ (rv * 100) | round(0) | int }}</dt>
          <dd>{{ job.score_fit.why }}</dd>
          {% endif %}
        </div>
      </td>
      <td class="narrow tip tip-score"
        data-sort="{{ cv }}">
        <span class="badge {{ score_class(cv) }}"
          >{{ (cv * 100) | round(0) | int }}</span>
        <div class="tip-body">
          {{ job.score_interest.why }}</div>
      </td>
      <td class="narrow tip tip-score"
        data-sort="{{ rv }}">
        {% if job.score_fit is not none %}
        <span class="badge {{ score_class(rv) }}"
          >{{ (rv * 100) | round(0) | int }}</span>
        <div class="tip-body">
          {{ job.score_fit.why }}</div>
        {% endif %}
      </td>
      <td class="date"
        data-sort="{{ epoch(job.posted) }}">
        {% if job.posted %}
        <span class="{{ date_class(job.posted) }}"
          title="{{ job.posted }}">
          {{- time_ago(job.posted) -}}
        </span>{% endif %}</td>
      <td class="cell">
        <a href="{{ job.url }}">
          {{- job.title -}}
        </a></td>
      <td class="cell">{{ job.company }}</td>
      <td class="conns tip"
        data-sort="{{ first | length }}">
        {{- first | length or "" -}}
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
        data-sort="{{ epoch(job.scraped_at) }}"
        title="{{ job.scraped_at }}">
        {{ time_ago(job.scraped_at) }}</td>
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
document.querySelectorAll('.tip')
  .forEach(function(el) {
    el.addEventListener('click', function(e) {
      document.querySelectorAll('.tip.active')
        .forEach(function(t) {
          if (t !== el)
            t.classList.remove('active');
        });
      this.classList.toggle('active');
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
  var PAGE_SIZE = 50;
  var curPage = 0;
  var pgPrev = document.getElementById('pg-prev');
  var pgNext = document.getElementById('pg-next');
  var pgInfo = document.getElementById('pg-info');
  var pager = document.getElementById('pager');
  var tbody = document.querySelector('tbody');
  function getRows() {
    return Array.from(
      tbody.querySelectorAll('tr'));
  }
  function showPage(page) {
    var rows = getRows();
    var total = rows.length;
    var numPages = Math.max(1,
      Math.ceil(total / PAGE_SIZE));
    curPage = Math.max(0,
      Math.min(page, numPages - 1));
    var start = curPage * PAGE_SIZE;
    var end = start + PAGE_SIZE;
    rows.forEach(function(r, i) {
      r.style.display =
        (i >= start && i < end) ? '' : 'none';
    });
    pgInfo.textContent = (start + 1)
      + '\\u2013' + Math.min(end, total)
      + ' of ' + total;
    pgPrev.disabled = curPage === 0;
    pgNext.disabled = curPage >= numPages - 1;
    pager.style.display =
      numPages <= 1 ? 'none' : '';
  }
  pgPrev.addEventListener('click', function() {
    showPage(curPage - 1);
  });
  pgNext.addEventListener('click', function() {
    showPage(curPage + 1);
  });
  initColumns([
    {name: 'Score', on: true},
    {name: 'Interest', on: false},
    {name: 'Fit', on: false},
    {name: 'Posted', on: true},
    {name: 'Title', on: true},
    {name: 'Company', on: true},
    {name: '1st', on: true},
    {name: '2nd', on: true},
    {name: 'Team', on: false},
    {name: 'Location', on: false},
    {name: 'Compensation', on: false},
    {name: 'Scraped', on: false}
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
        dt = datetime.fromisoformat(date_str).replace(
            tzinfo=UTC
        )
    except ValueError:
        return ""
    days = (datetime.now(UTC) - dt).days
    if days < 7:
        return "badge badge-green"
    return ""


def _score_class(score: float) -> str:
    if score >= 0.7:
        return "badge-green"
    if score >= 0.4:
        return "badge-yellow"
    return "badge-red"


def _no_connections(
    company: str,
) -> tuple[list[Connection], list[SecondDegree]]:
    return [], []


def render_report(
    jobs: list[ScoredJob],
    path: Path,
    lookup: LookupFn | None = None,
) -> None:
    env = Environment(autoescape=True)
    template = env.from_string(TEMPLATE)
    now = datetime.now(UTC).isoformat()
    html = template.render(
        jobs=jobs,
        generated_at=now,
        score_class=_score_class,
        date_class=_date_class,
        time_ago=_time_ago,
        epoch=_epoch,
        lookup=lookup or _no_connections,
        base_css=BASE_CSS,
        base_js=BASE_JS,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
