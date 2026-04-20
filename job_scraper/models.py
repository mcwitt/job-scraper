from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Job:
    hash: str
    title: str
    company: str
    url: str
    description: str
    source: str
    last_seen_at: str = ""
    team: str | None = None
    posted: str | None = None
    comp: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class Interest:
    strengths_alignment: str
    growth_opportunities: str
    role_type_fit: str
    industry_alignment: str
    company_reputation: str
    compensation: str
    location: str
    summary: str
    score: int


@dataclass(frozen=True)
class Fit:
    demonstrated_experience: str
    institutional_credibility: str
    depth_vs_adjacency: str
    career_trajectory: str
    minimum_qualifications: str
    seniority_alignment: str
    location_visa: str
    summary: str
    score: int


@dataclass(frozen=True)
class Score:
    interest: Interest
    fit: Fit


@dataclass(frozen=True)
class ScoredJob:
    hash: str
    title: str
    company: str
    url: str
    description: str
    source: str
    score_interest: Interest
    score_fit: Fit
    last_seen_at: str = ""
    team: str | None = None
    posted: str | None = None
    comp: str | None = None
    location: str | None = None


to_dict = asdict