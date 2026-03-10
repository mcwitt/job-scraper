from datetime import datetime, timezone
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
  th, td { padding: 0.75rem 1rem; text-align: left; border-bottom: 1px solid #eee; }
  th { background: #fafafa; font-weight: 600; position: sticky; top: 0;
    cursor: pointer; user-select: none; white-space: nowrap; }
  th::after { content: ""; display: inline-block; width: 0.6em; margin-left: 0.3em; }
  th.sort-asc::after { content: "\\25B2"; font-size: 0.6em; vertical-align: middle; }
  th.sort-desc::after { content: "\\25BC"; font-size: 0.6em; vertical-align: middle; }
  tr:hover { background: #f9f9f9; }
  a { color: #0066cc; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .score { font-weight: 700; padding: 0.25rem 0.5rem;
    border-radius: 4px; display: inline-block;
    min-width: 2.5rem; text-align: center; }
  .score-high { background: #d4edda; color: #155724; }
  .score-mid { background: #fff3cd; color: #856404; }
  .score-low { background: #f8d7da; color: #721c24; }
  .tip { position: relative; cursor: help; }
  .tip .tip-body { display: none; position: absolute; left: 0; top: 100%;
    z-index: 10; background: white; border: 1px solid #ddd;
    border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    padding: 0.75rem; min-width: 320px; max-width: 420px;
    font-weight: normal; font-size: 0.85em; color: #333;
    white-space: normal; }
  .tip:hover .tip-body, .tip.active .tip-body { display: block; }
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
</style>
</head>
<body>
<h1>Job Scraper Report</h1>
<p class="meta" style="margin-bottom: 1rem;">{{ jobs | length }} jobs scored</p>
<table>
  <thead>
    <tr>
      <th title="Geometric mean of Candidate and Recruiter scores">Score</th>
      <th data-sort-desc>Posted</th>
      <th>Title</th>
      <th>Company</th>
      <th title="1st-degree LinkedIn connections at this company">1st</th>
      <th title="2nd-degree LinkedIn connections at this company">2nd</th>
      <th>Team</th>
      <th>Location</th>
      <th>Comp.</th>
    </tr>
  </thead>
  <tbody>
    {% for job in jobs %}
    {% set cv = job.fit_candidate.value %}
    {% set rv = job.fit_recruiter.value if job.fit_recruiter else cv %}
    {% set score = (cv * rv) ** 0.5 %}
    {% set first, second = lookup(job.company) %}
    <tr>
      <td class="tip" data-sort="{{ score }}">
        <span class="score {{ score_class(score) }}"
          >{{ (score * 100) | round(0) | int }}</span>
        <dl class="tip-body">
          <dt>Candidate: {{ (cv * 100) | round(0) | int }}</dt>
          <dd>{{ job.fit_candidate.why }}</dd>
          {% if job.fit_recruiter is not none %}
          <dt>Recruiter: {{ (rv * 100) | round(0) | int }}</dt>
          <dd>{{ job.fit_recruiter.why }}</dd>
          {% endif %}
        </dl>
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
        {%- for c in first %}
          <a href="{{ c.url }}">{{ c.name }}</a>
          {{- ", " if not loop.last -}}
        {%- endfor %}
        </div>
        {%- endif %}
      </td>
      {% set n2 = second | map(attribute='connections') | map('length') | sum %}
      <td class="conns tip" data-sort="{{ n2 }}">
        {{- n2 or "" -}}
        {%- if second %}
        <div class="tip-body">
        {%- for g in second %}
          <a href="{{ g.via.url }}">{{ g.via.name }}</a>:
          {%- for c in g.connections %}
          <a href="{{ c.url }}">{{ c.name }}</a>
          {{- ", " if not loop.last -}}
          {%- endfor %}
          {{ "<br>" if not loop.last }}
        {%- endfor %}
        </div>
        {%- endif %}
      </td>
      <td class="cell">{{ job.team or "" }}</td>
      <td class="cell">{{ job.location or "" }}</td>
      <td class="cell">{{ job.comp or "" }}</td>
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
document.addEventListener('click', function() {
  document.querySelectorAll('.tip.active').forEach(function(t) {
    t.classList.remove('active');
  });
});
(function() {
  var table = document.querySelector('table');
  var thead = table.querySelector('thead');
  var tbody = table.querySelector('tbody');
  var ths = thead.querySelectorAll('th');
  ths.forEach(function(th, col) {
    th.addEventListener('click', function() {
      var desc = th.hasAttribute('data-sort-desc');
      var asc = desc
        ? th.classList.contains('sort-desc')
        : !th.classList.contains('sort-asc');
      ths.forEach(function(h) { h.classList.remove('sort-asc', 'sort-desc'); });
      th.classList.add(asc ? 'sort-asc' : 'sort-desc');
      var rows = Array.from(tbody.querySelectorAll('tr'));
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
    });
  });
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
            tzinfo=timezone.utc
        )
    except ValueError:
        return date_str
    delta = datetime.now(timezone.utc) - dt
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
            tzinfo=timezone.utc
        )
    except ValueError:
        return ""
    days = (datetime.now(timezone.utc) - dt).days
    if days < 7:
        return "age-fresh"
    return ""


def _epoch(date_str: str | None) -> int:
    if not date_str:
        return 0
    try:
        dt = datetime.fromisoformat(date_str).replace(
            tzinfo=timezone.utc
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
    html = template.render(
        jobs=jobs,
        score_class=_score_class,
        date_class=_date_class,
        time_ago=_time_ago,
        epoch=_epoch,
        lookup=lookup or _no_connections,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
