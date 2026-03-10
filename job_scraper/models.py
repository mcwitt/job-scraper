from dataclasses import asdict, dataclass
from typing import Any

import dacite


@dataclass(frozen=True)
class Job:
    hash: str
    title: str
    company: str
    url: str
    description: str
    source: str
    scraped_at: str
    team: str | None = None
    posted: str | None = None
    comp: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class Score:
    value: float
    why: str


@dataclass(frozen=True)
class ScoredJob:
    hash: str
    title: str
    company: str
    url: str
    description: str
    source: str
    scraped_at: str
    score_interest: Score
    team: str | None = None
    posted: str | None = None
    comp: str | None = None
    location: str | None = None
    score_fit: Score | None = None


_DACITE = dacite.Config(strict=True)

to_dict = asdict


def from_dict[T](cls: type[T], d: dict[str, Any]) -> T:
    return dacite.from_dict(cls, d, config=_DACITE)


def scored_job(
    job: Job,
    score_interest: Score,
    score_fit: Score | None = None,
) -> ScoredJob:
    return ScoredJob(
        **to_dict(job),
        score_interest=score_interest,
        score_fit=score_fit,
    )
