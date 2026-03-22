import asyncio
import hashlib
import logging

import anthropic

from job_scraper.cache import Cache
from job_scraper.llm import create

logger = logging.getLogger(__name__)

_INTEREST_META = """\
You are generating a scoring rubric that will be used to evaluate \
job postings from this candidate's perspective. The rubric must be \
specific enough that a different model can apply it consistently \
across many jobs without re-reading the candidate's preferences.

Produce a rubric with these sections. For each section, convert \
the candidate's preferences into concrete decision rules with \
point allocations.

## Sections

1. **strengths_alignment** (assess how the role leverages existing \
strengths)
2. **growth_opportunities** (assess development in areas of stated \
interest — interest matters even without professional experience)
3. **role_type_fit** (IC vs management, seniority, day-to-day work. \
If the role type is a hard mismatch — e.g. management-only when \
candidate wants IC — score 0.)
4. **industry_alignment** (does the company's domain/industry align \
with the candidate's interests and values? If the candidate has \
stated aversions to specific industries, score 0 for those.)
5. **company_reputation** (is the company or team well-regarded in \
a field the candidate cares about?)
6. **compensation** (fit with expected band)
7. **location** (compatibility with stated location preferences. \
If relocation is required and candidate has stated no relocation, \
score 0.)

## Format

For each section, write:
- What to look for (specific to this candidate)
- Scoring bands with point ranges, e.g.:
  "If the role is fully remote or in [preferred cities]: 8-10.
   If hybrid in an acceptable city: 5-7.
   If requires relocation to a non-preferred location: 0-3.
   If requires relocation and candidate said no relocation: 0."

## Final score guidance

Provide point allocation weights across sections (must sum to 100). \
Weight aspirations and interests heavily — a role in an area of \
strong stated interest should score well even without professional \
experience. Increase weights for categories where the candidate has \
expressed strong preferences or hard constraints.

Score anchors:
- 90-100: Thrilled — strong alignment with strengths and growth \
interests
- 70-89: Genuinely appealing — good fit on most dimensions
- 40-69: Mixed — some appeal but significant preference gaps
- 0-39: Not interesting — poor alignment with what they want

## Candidate Preferences

"""

_BRIEF_META = """\
You are distilling a candidate's resume into a concise brief that \
a recruiter model will use to assess fit against many different \
job postings. Extract the facts — do not score or weight anything.

## Sections

1. **demonstrated_experience** — Professional roles, what they \
built/shipped, technologies used on the job. Distinguish deep \
expertise from brief exposure.
2. **institutional_credibility** — Employer names and reputation, \
degrees, affiliations, notable projects or publications.
3. **career_trajectory** — Progression pattern: steady climb, \
pivot, generalist broadening, etc. Current level and direction.
4. **hard_qualifications** — Total years of experience, specific \
technologies/languages with approximate years, degrees and \
certifications.
5. **location_visa** — Current location, remote/relocation \
preferences, visa status if mentioned.

## Format

For each section, write a concise factual summary using only \
information from the resume. No scoring, no speculation, no \
assessment of fit. Just the facts a recruiter needs.

## Candidate Resume

"""


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def generate_interest_rubric(
    preferences: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    semaphore: asyncio.Semaphore,
) -> str:
    key = _cache_key(_INTEREST_META + preferences)
    logger.info("interest rubric key=%s", key)
    return await create(
        client,
        model,
        cache,
        key,
        semaphore,
        system="Generate a candidate-specific interest scoring rubric.",
        messages=[
            {"role": "user", "content": _INTEREST_META + preferences}
        ],
    )


async def generate_candidate_brief(
    resume: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    semaphore: asyncio.Semaphore,
) -> str:
    key = _cache_key(_BRIEF_META + resume)
    logger.info("candidate brief key=%s", key)
    return await create(
        client,
        model,
        cache,
        key,
        semaphore,
        system="Distill a candidate resume into a recruiter brief.",
        messages=[
            {"role": "user", "content": _BRIEF_META + resume}
        ],
    )
