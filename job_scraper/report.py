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
  th { background: #fafafa; font-weight: 600; position: sticky; top: 0; }
  tr:hover { background: #f9f9f9; }
  a { color: #0066cc; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .score { font-weight: 700; padding: 0.25rem 0.5rem;
    border-radius: 4px; display: inline-block;
    min-width: 2.5rem; text-align: center; }
  .score-high { background: #d4edda; color: #155724; }
  .score-mid { background: #fff3cd; color: #856404; }
  .score-low { background: #f8d7da; color: #721c24; }
  .cell { max-width: 300px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; cursor: default; }
  .cell.expanded { white-space: normal; overflow: visible; }
  .why { max-width: 450px; font-size: 0.9em; color: #555; }
  .conns { font-size: 0.82em; max-width: 200px; }
  .conns a { color: #0066cc; }
  .date { white-space: nowrap; font-size: 0.85em; }
  .age-fresh { font-weight: 700; padding: 0.25rem 0.5rem;
    border-radius: 4px; background: #d4edda; color: #155724; }
  .age-stale { font-weight: 700; padding: 0.25rem 0.5rem;
    border-radius: 4px; background: #f8d7da; color: #721c24; }
  .meta { font-size: 0.85em; color: #777; }
</style>
</head>
<body>
<h1>Job Scraper Report</h1>
<p class="meta" style="margin-bottom: 1rem;">{{ jobs | length }} jobs scored</p>
<table>
  <thead>
    <tr>
      <th>Score</th>
      <th>Posted</th>
      <th>Title</th>
      <th>Company</th>
      <th>Team</th>
      <th>Location</th>
      <th>1st</th>
      <th>2nd</th>
      <th>Why</th>
    </tr>
  </thead>
  <tbody>
    {% for job in jobs %}
    {% set pct = (job.score * 100) | round(0) | int %}
    {% set first, second = lookup(job.company) %}
    <tr>
      <td><span class="score {{ score_class(job.score) }}">{{ pct }}</span></td>
      <td class="date">{% if job.posted %}
        <span class="age {{ date_class(job.posted) }}">
          {{- time_ago(job.posted) -}}
        </span>{% endif %}</td>
      <td class="cell"><a href="{{ job.url }}">{{ job.title }}</a></td>
      <td class="cell">{{ job.company }}</td>
      <td class="cell">{{ job.team or "" }}</td>
      <td class="cell">{{ job.location or "" }}</td>
      <td class="conns cell">
        {%- for c in first -%}
        <a href="{{ c.url }}">{{ c.name }}</a>
        {{- ", " if not loop.last -}}
        {%- endfor -%}
      </td>
      <td class="conns cell">
        {%- for g in second -%}
        <a href="{{ g.via.url }}">{{ g.via.name }}</a>:
        {%- for c in g.connections %} <a href="{{ c.url }}">{{ c.name }}</a>
        {{- ", " if not loop.last -}}
        {%- endfor -%}
        {{ "<br>" if not loop.last }}
        {%- endfor -%}
      </td>
      <td class="cell why">{{ job.why }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<script>
document.querySelectorAll('.cell').forEach(function(el) {
  el.addEventListener('click', function() { this.classList.toggle('expanded'); });
});
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
    if days >= 90:
        return "age-stale"
    return ""


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
        lookup=lookup or _no_connections,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
