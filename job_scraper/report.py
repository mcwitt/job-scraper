from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment

from job_scraper.linkedin import Connection, LookupFn, SecondDegree
from job_scraper.models import ScoredJob

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Scraper Report</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; background: #f5f5f5; padding: 2rem; }
  h1 { margin-bottom: 1.5rem; }
  table { width: 100%; border-collapse: collapse; background: white;
    border-radius: 8px; overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  table { font-size: 0.85em; }
  th, td { padding: 0.4rem 0.6rem; text-align: left; border-bottom: 1px solid #eee; }
  th { background: #fafafa; font-weight: 600; position: sticky; top: 0;
    cursor: pointer; user-select: none; white-space: nowrap; }
  th::after { content: ""; display: inline-block; width: 0.6em; margin-left: 0.3em; }
  th.sort-asc::after { content: "\\25B2"; font-size: 0.6em; vertical-align: middle; }
  th.sort-desc::after { content: "\\25BC"; font-size: 0.6em; vertical-align: middle; }
  tr:hover { background: #f9f9f9; }
  a { color: #0066cc; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .narrow { padding-left: 0.3rem; padding-right: 0.3rem;
    text-align: center; white-space: nowrap; }
  .score { font-weight: 700; padding: 0.15rem 0.35rem;
    border-radius: 4px; display: inline-block; text-align: center; }
  .score-high { background: #d4edda; color: #155724; }
  .score-mid { background: #fff3cd; color: #856404; }
  .score-low { background: #f8d7da; color: #721c24; }
  .tip { position: relative; cursor: help; }
  .tip .tip-body { display: none; position: absolute; left: 0; top: 100%;
    z-index: 10; background: white; border: 1px solid #ddd;
    border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    padding: 0.75rem; max-width: 420px;
    font-weight: normal; font-size: 0.85em; color: #333;
    white-space: normal; text-align: left; }
  .tip:hover .tip-body, .tip.active .tip-body { display: block; }
  .tip-score .tip-body { min-width: 320px; }
  .tip-body ul { margin: 0; padding-left: 1.2em; }
  .tip-body li { margin: 0.15rem 0; }
  .tip-body dt { font-weight: 600; margin-top: 0.4rem; }
  .tip-body dt:first-child { margin-top: 0; }
  .tip-body dd { margin: 0.1rem 0 0 0; color: #555; }
  .cell { max-width: 300px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; cursor: default; }
  .cell.expanded { white-space: normal; overflow: visible; }
  .conns { text-align: center; }
  .conns a { color: #0066cc; }
  .date { white-space: nowrap; font-size: 0.85em; }
  .age-fresh { font-weight: 700; padding: 0.25rem 0.5rem;
    border-radius: 4px; background: #d4edda; color: #155724; }
  .meta { font-size: 0.85em; color: #777; }
  .col-toggle { position: relative; display: inline-block; margin-bottom: 1rem; }
  .btn { font: inherit; font-size: 0.85em; padding: 0.35rem 0.7rem;
    background: white; border: 1px solid #ccc; border-radius: 6px; cursor: pointer; }
  .btn:hover:not(:disabled) { background: #f0f0f0; }
  .col-panel { display: none; position: absolute; left: 0; top: 100%;
    margin-top: 0.3rem; z-index: 20; background: white; border: 1px solid #ddd;
    border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.12);
    padding: 0.5rem 0; min-width: 150px; }
  .col-panel.open { display: block; }
  .col-panel label { display: block; padding: 0.25rem 0.75rem; font-size: 0.85em;
    cursor: pointer; white-space: nowrap; }
  .col-panel label:hover { background: #f5f5f5; }
  .pager { display: flex; align-items: center; gap: 0.5rem;
    margin-bottom: 1rem; font-size: 0.85em; }
  .btn:disabled { opacity: 0.4; cursor: default; }
</style>
</head>
<body>
<h1>Job Scraper Report</h1>
<p class="meta" style="margin-bottom: 1rem;"
  >{{ jobs | length }} jobs scored &middot; Generated {{ generated_ago }}</p>
<div style="display: flex; gap: 1rem; align-items: start; flex-wrap: wrap;">
  <div class="col-toggle">
    <button class="btn" id="col-btn">Columns &#9662;</button>
    <div class="col-panel" id="col-panel"></div>
  </div>
  <div class="pager" id="pager">
    <button class="btn" id="pg-prev">&lsaquo; Prev</button>
    <span id="pg-info"></span>
    <button class="btn" id="pg-next">Next &rsaquo;</button>
  </div>
</div>
<table>
  <thead>
    <tr>
      <th class="narrow" title="Geometric mean of Interest and Fit scores">Score</th>
      <th class="narrow" title="Interest to candidate">Interest</th>
      <th class="narrow" title="Fit for role">Fit</th>
      <th data-sort-desc>Posted</th>
      <th>Title</th>
      <th>Company</th>
      <th title="1st-degree LinkedIn connections at this company">1st</th>
      <th title="2nd-degree LinkedIn connections at this company">2nd</th>
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
      <td class="narrow tip tip-score" data-sort="{{ score }}">
        <span class="score {{ score_class(score) }}"
          >{{ (score * 100) | round(0) | int }}</span>
        <div class="tip-body">
          <dt>Interest {{ (cv * 100) | round(0) | int }}</dt>
          <dd>{{ job.score_interest.why }}</dd>
          {% if job.score_fit is not none %}
          <dt>Fit {{ (rv * 100) | round(0) | int }}</dt>
          <dd>{{ job.score_fit.why }}</dd>
          {% endif %}
        </div>
      </td>
      <td class="narrow tip tip-score" data-sort="{{ cv }}">
        <span class="score {{ score_class(cv) }}"
          >{{ (cv * 100) | round(0) | int }}</span>
        <div class="tip-body">{{ job.score_interest.why }}</div>
      </td>
      <td class="narrow tip tip-score" data-sort="{{ rv }}">
        {% if job.score_fit is not none %}<span class="score {{ score_class(rv) }}"
          >{{ (rv * 100) | round(0) | int }}</span>
        <div class="tip-body">{{ job.score_fit.why }}</div>
        {% endif %}
      </td>
      <td class="date" data-sort="{{ epoch(job.posted) }}">{% if job.posted %}
        <span class="age {{ date_class(job.posted) }}"
          title="{{ job.posted }}">
          {{- time_ago(job.posted) -}}
        </span>{% endif %}</td>
      <td class="cell"><a href="{{ job.url }}">{{ job.title }}</a></td>
      <td class="cell">{{ job.company }}</td>
      <td class="conns tip" data-sort="{{ first | length }}">
        {{- first | length or "" -}}
        {%- if first %}
        <div class="tip-body">
        <ul>
        {%- for c in first %}
          <li><a href="{{ c.url }}">{{ c.name }}</a></li>
        {%- endfor %}
        </ul>
        </div>
        {%- endif %}
      </td>
      {% set n2 = second | map(attribute='connections') | map('length') | sum %}
      <td class="conns tip" data-sort="{{ n2 }}">
        {{- n2 or "" -}}
        {%- if second %}
        <div class="tip-body">
        <ul>
        {%- for g in second %}
          <li><a href="{{ g.via.url }}">{{ g.via.name }}</a>
            <ul>
            {%- for c in g.connections %}
              <li><a href="{{ c.url }}">{{ c.name }}</a></li>
            {%- endfor %}
            </ul>
          </li>
        {%- endfor %}
        </ul>
        </div>
        {%- endif %}
      </td>
      <td class="cell">{{ job.team or "" }}</td>
      <td class="cell">{{ job.location or "" }}</td>
      <td class="cell">{{ job.comp or "" }}</td>
      <td class="date" data-sort="{{ epoch(job.scraped_at) }}"
        title="{{ job.scraped_at }}">{{ time_ago(job.scraped_at) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<script>
document.querySelectorAll('.cell').forEach(function(el) {
  el.addEventListener('click', function() { this.classList.toggle('expanded'); });
});
document.querySelectorAll('.tip').forEach(function(el) {
  el.addEventListener('click', function(e) {
    document.querySelectorAll('.tip.active').forEach(function(t) {
      if (t !== el) t.classList.remove('active');
    });
    this.classList.toggle('active');
    e.stopPropagation();
  });
});
document.addEventListener('click', function(e) {
  document.querySelectorAll('.tip.active').forEach(function(t) {
    t.classList.remove('active');
  });
  var toggle = document.querySelector('.col-toggle');
  if (!toggle.contains(e.target)) {
    document.getElementById('col-panel').classList.remove('open');
  }
});
(function() {
  var COLS = [
    {name: 'Score', on: true}, {name: 'Interest', on: false},
    {name: 'Fit', on: false}, {name: 'Posted', on: true},
    {name: 'Title', on: true}, {name: 'Company', on: true},
    {name: '1st', on: true}, {name: '2nd', on: true},
    {name: 'Team', on: false}, {name: 'Location', on: false},
    {name: 'Compensation', on: false},
    {name: 'Scraped', on: false}
  ];
  var KEY = 'job-scraper-cols';
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(KEY)); } catch(e) {}
  if (saved && typeof saved === 'object') {
    COLS.forEach(function(c) {
      if (saved.hasOwnProperty(c.name)) c.on = saved[c.name];
    });
  }
  var style = document.createElement('style');
  document.head.appendChild(style);
  function apply() {
    var rules = [];
    var state = {};
    COLS.forEach(function(c, i) {
      state[c.name] = c.on;
      if (!c.on) {
        var n = i + 1;
        rules.push('th:nth-child(' + n + '),td:nth-child(' + n
          + '){display:none}');
      }
    });
    style.textContent = rules.join('');
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch(e) {}
  }
  var panel = document.getElementById('col-panel');
  COLS.forEach(function(c, i) {
    var lbl = document.createElement('label');
    var cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = c.on;
    cb.addEventListener('change', function() { c.on = cb.checked; apply(); });
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(' ' + c.name));
    panel.appendChild(lbl);
  });
  document.getElementById('col-btn').addEventListener('click', function(e) {
    panel.classList.toggle('open');
    e.stopPropagation();
  });
  apply();
})();
(function() {
  var table = document.querySelector('table');
  var thead = table.querySelector('thead');
  var tbody = table.querySelector('tbody');
  var ths = thead.querySelectorAll('th');
  var PAGE_SIZE = 50;
  var curPage = 0;
  var pgPrev = document.getElementById('pg-prev');
  var pgNext = document.getElementById('pg-next');
  var pgInfo = document.getElementById('pg-info');
  var pager = document.getElementById('pager');

  function getRows() {
    return Array.from(tbody.querySelectorAll('tr'));
  }

  function showPage(page) {
    var rows = getRows();
    var total = rows.length;
    var numPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    curPage = Math.max(0, Math.min(page, numPages - 1));
    var start = curPage * PAGE_SIZE;
    var end = start + PAGE_SIZE;
    rows.forEach(function(r, i) {
      r.style.display = (i >= start && i < end) ? '' : 'none';
    });
    pgInfo.textContent = (start + 1) + '\u2013' + Math.min(end, total)
      + ' of ' + total;
    pgPrev.disabled = curPage === 0;
    pgNext.disabled = curPage >= numPages - 1;
    pager.style.display = numPages <= 1 ? 'none' : '';
  }

  pgPrev.addEventListener('click', function() { showPage(curPage - 1); });
  pgNext.addEventListener('click', function() { showPage(curPage + 1); });

  ths.forEach(function(th, col) {
    th.addEventListener('click', function() {
      var desc = th.hasAttribute('data-sort-desc');
      var asc = desc
        ? th.classList.contains('sort-desc')
        : !th.classList.contains('sort-asc');
      ths.forEach(function(h) { h.classList.remove('sort-asc', 'sort-desc'); });
      th.classList.add(asc ? 'sort-asc' : 'sort-desc');
      var rows = getRows();
      rows.sort(function(a, b) {
        var ac = a.children[col], bc = b.children[col];
        var av = ac.dataset.sort != null
          ? ac.dataset.sort : ac.textContent.trim();
        var bv = bc.dataset.sort != null
          ? bc.dataset.sort : bc.textContent.trim();
        var an = parseFloat(av), bn = parseFloat(bv);
        var cmp = (!isNaN(an) && !isNaN(bn)) ? an - bn : av.localeCompare(bv);
        return asc ? cmp : -cmp;
      });
      rows.forEach(function(r) { tbody.appendChild(r); });
      showPage(0);
    });
  });

  showPage(0);
})();
</script>
</body>
</html>
"""


def _time_ago(date_str: str | None) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str).replace(
            tzinfo=UTC
        )
    except ValueError:
        return date_str
    delta = datetime.now(UTC) - dt
    seconds = int(delta.total_seconds())
    if seconds < 3600:
        n = max(seconds // 60, 1)
        return f"{n} min ago" if n == 1 else f"{n} mins ago"
    if seconds < 86400:
        n = seconds // 3600
        return f"{n} hour ago" if n == 1 else f"{n} hours ago"
    days = seconds // 86400
    if days < 7:
        return f"{days} day ago" if days == 1 else f"{days} days ago"
    if days < 30:
        n = days // 7
        return f"{n} week ago" if n == 1 else f"{n} weeks ago"
    if days < 365:
        n = days // 30
        return f"{n} month ago" if n == 1 else f"{n} months ago"
    n = days // 365
    return f"{n} year ago" if n == 1 else f"{n} years ago"



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
        return "age-fresh"
    return ""


def _epoch(date_str: str | None) -> int:
    if not date_str:
        return 0
    try:
        dt = datetime.fromisoformat(date_str).replace(
            tzinfo=UTC
        )
        return int(dt.timestamp())
    except ValueError:
        return 0


def _score_class(score: float) -> str:
    if score >= 0.7:
        return "score-high"
    if score >= 0.4:
        return "score-mid"
    return "score-low"


def _no_connections(company: str) -> tuple[list[Connection], list[SecondDegree]]:
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
        generated_ago=_time_ago(now),
        score_class=_score_class,
        date_class=_date_class,
        time_ago=_time_ago,
        epoch=_epoch,
        lookup=lookup or _no_connections,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
