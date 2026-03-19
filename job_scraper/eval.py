import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from job_scraper.models import Job, ScoredJob

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Eval Report</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1100px;
    margin: 2rem auto; padding: 0 1rem; color: #222; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.3rem; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ border: 1px solid #ddd; padding: 0.45rem 0.65rem;
    text-align: left; vertical-align: top; }}
  th {{ background: #f5f5f5; font-weight: 600; white-space: nowrap; }}
  tr.pass td {{ background: #f0fff4; }}
  tr.fail td {{ background: #fff0f0; }}
  .badge {{ display: inline-block; padding: 0.1em 0.5em;
    border-radius: 4px; font-weight: 700; font-size: 0.85em; }}
  .badge-pass {{ background: #bbf7d0; color: #14532d; }}
  .badge-fail {{ background: #fecaca; color: #7f1d1d; }}
  .url {{ font-size: 0.8em; word-break: break-all; }}
  .rank-missing {{ color: #999; font-style: italic; }}
  .summary {{ margin-bottom: 1rem; font-size: 1rem; }}
</style>
</head>
<body>
<h1>Eval Report</h1>
<p class="meta">Generated {generated_at} &middot; {n_pass}/{n_total} passed</p>
<p class="summary">{summary_badge}</p>
<table>
  <thead>
    <tr>
      <th>Stage</th>
      <th>Label</th>
      <th>URL</th>
      <th>Top-K</th>
      <th>Expected</th>
      <th>Rank</th>
      <th>Result</th>
    </tr>
  </thead>
  <tbody>
{rows}
  </tbody>
</table>
</body>
</html>
"""

ROW_TEMPLATE = """\
    <tr class="{row_class}">
      <td>{stage}</td>
      <td>{label}</td>
      <td class="url"><a href="{url}">{url}</a></td>
      <td>{k}</td>
      <td>{expected}</td>
      <td>{rank}</td>
      <td><span class="badge {badge_class}">{result}</span></td>
    </tr>"""


@dataclass(frozen=True)
class Assertion:
    url: str
    k: int
    in_top_k: bool
    label: str


@dataclass(frozen=True)
class EvalResult:
    assertion: Assertion
    stage: str  # "filter" or "score"
    passed: bool
    found: bool  # False if job URL not found in pipeline output
    rank: int | None  # 1-based rank, None if not found


def load_assertions(path: Path) -> tuple[list[Assertion], list[Assertion]]:
    """Load evals.toml → (filter_assertions, score_assertions)."""
    data = tomllib.loads(path.read_text())
    filter_assertions = [
        Assertion(
            url=entry["url"],
            k=entry["k"],
            in_top_k=entry["in_top_k"],
            label=entry.get("label", ""),
        )
        for entry in data.get("filter", [])
    ]
    score_assertions = [
        Assertion(
            url=entry["url"],
            k=entry["k"],
            in_top_k=entry["in_top_k"],
            label=entry.get("label", ""),
        )
        for entry in data.get("score", [])
    ]
    return filter_assertions, score_assertions


def _eval_list(
    assertions: list[Assertion],
    stage: str,
    urls: list[str],
) -> list[EvalResult]:
    results = []
    for assertion in assertions:
        try:
            rank = urls.index(assertion.url) + 1  # 1-based
            found = True
        except ValueError:
            rank = None
            found = False
        passed = found and (rank <= assertion.k) == assertion.in_top_k
        results.append(
            EvalResult(
                assertion=assertion,
                stage=stage,
                passed=passed,
                found=found,
                rank=rank,
            )
        )
    return results


def run_filter_evals(
    assertions: list[Assertion],
    scored: list[tuple[Job, float]],
) -> list[EvalResult]:
    """Check filter assertions against relevance-ranked jobs (sorted desc)."""
    urls = [job.url for job, _ in scored]
    return _eval_list(assertions, "filter", urls)


def run_score_evals(
    assertions: list[Assertion],
    jobs: list[ScoredJob],
) -> list[EvalResult]:
    """Check score assertions against interest-score-ranked jobs (sorted desc)."""
    urls = [job.url for job in jobs]
    return _eval_list(assertions, "score", urls)


def render_eval_report(results: list[EvalResult], path: Path) -> None:
    """Write eval_report.html — table of pass/fail per assertion."""
    rows = []
    for r in results:
        if r.rank is not None:
            rank_str = str(r.rank)
        else:
            rank_str = '<span class="rank-missing">not found</span>'
        rows.append(
            ROW_TEMPLATE.format(
                row_class="pass" if r.passed else "fail",
                stage=r.stage,
                label=r.assertion.label,
                url=r.assertion.url,
                k=r.assertion.k,
                expected="in top-k" if r.assertion.in_top_k else "not in top-k",
                rank=rank_str,
                badge_class="badge-pass" if r.passed else "badge-fail",
                result="✓" if r.passed else "✗",
            )
        )

    n_pass = sum(r.passed for r in results)
    n_total = len(results)
    if n_pass == n_total:
        summary_badge = (
            f'<span class="badge badge-pass">All {n_total} passed</span>'
        )
    else:
        summary_badge = (
            f'<span class="badge badge-fail">'
            f"{n_pass}/{n_total} passed</span>"
        )

    html = TEMPLATE.format(
        generated_at=datetime.now(UTC).isoformat(),
        n_pass=n_pass,
        n_total=n_total,
        summary_badge=summary_badge,
        rows="\n".join(rows),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
