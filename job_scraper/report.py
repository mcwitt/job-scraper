from pathlib import Path

from jinja2 import Environment

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
  .why { max-width: 300px; font-size: 0.9em; color: #555; }
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
      <th>Title</th>
      <th>Company</th>
      <th>Team</th>
      <th>Location</th>
      <th>Why</th>
    </tr>
  </thead>
  <tbody>
    {% for job in jobs %}
    <tr>
      <td><span class="score {{ score_class(job.score) }}">{{ job.score }}</span></td>
      <td><a href="{{ job.url }}">{{ job.title }}</a></td>
      <td>{{ job.company }}</td>
      <td>{{ job.team or "" }}</td>
      <td>{{ job.location or "" }}</td>
      <td class="why">{{ job.why }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
</body>
</html>
"""


def _score_class(score: int) -> str:
    if score >= 70:
        return "score-high"
    if score >= 40:
        return "score-mid"
    return "score-low"


def render_report(jobs: list[ScoredJob], path: Path) -> None:
    env = Environment(autoescape=True)
    template = env.from_string(TEMPLATE)
    html = template.render(jobs=jobs, score_class=_score_class)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
