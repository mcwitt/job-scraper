from dataclasses import dataclass, fields
from typing import Any


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

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass(frozen=True)
class ScoredJob:
    hash: str
    title: str
    company: str
    url: str
    description: str
    source: str
    scraped_at: str
    score: int
    why: str
    team: str | None = None
    posted: str | None = None
    comp: str | None = None
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScoredJob":
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})


def scored_job(job: Job, score: int, why: str) -> ScoredJob:
    return ScoredJob(**job.to_dict(), score=score, why=why)
