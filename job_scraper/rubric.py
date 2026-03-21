import asyncio
import hashlib
import logging
from datetime import date

import anthropic
from anthropic.types import TextBlock

from job_scraper.cache import Cache

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

_FIT_META = """\
The current month is {month}.

You are generating a scoring rubric that will be used by a \
recruiter model to assess how well this candidate fits specific job \
postings at {company}. The rubric must be specific enough that a \
different model can apply it consistently across many jobs without \
re-reading the full resume.

Produce a rubric with these sections. For each section, summarize \
the candidate's relevant qualifications and provide concrete \
decision rules.

## Sections

1. **demonstrated_experience** (professional, on-the-job experience \
— weight far more than stated interests or hobby projects)
2. **institutional_credibility** (reputation of employers, \
institutions, affiliations)
3. **depth_vs_adjacency** (deep expertise vs adjacent/related \
experience)
4. **career_trajectory** (progression toward this type of role, or \
significant pivot?)
5. **minimum_qualifications** (years of experience, technologies, \
degrees)
6. **seniority_alignment** (candidate's level match)
7. **location_visa** (logistical considerations)

## Format

For each section, write:
- The candidate's relevant background (specific facts from resume)
- Scoring bands with point ranges, e.g.:
  "If the role requires 5+ years of ML and candidate has 7 years \
at [companies]: 8-10.
   If the role requires adjacent skills the candidate has some \
exposure to: 4-6.
   If the role requires skills the candidate lacks entirely: 0-3."

{company_context_section}

## Final score guidance

Provide point allocation weights across sections (must sum to 100). \
Assess what is verifiable on paper, not aspirations.

Score anchors:
- 90-100: Immediately schedule a screen — strong demonstrated fit
- 70-89: Likely advance — most requirements clearly met on paper
- 40-69: Borderline — some gaps, worth considering if pool thin
- 0-39: Would not advance — significant gaps

## Candidate Resume

"""


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _generate(
    system: str,
    user: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    semaphore: asyncio.Semaphore,
) -> str:
    async with semaphore:
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    text_block = next(
        b for b in response.content if isinstance(b, TextBlock)
    )
    return text_block.text


async def generate_interest_rubric(
    preferences: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    semaphore: asyncio.Semaphore,
) -> str:
    key = _cache_key(_INTEREST_META + preferences)
    cached = cache.get(key)
    if cached is not None:
        logger.info("interest rubric cached")
        return cached["rubric"]

    logger.info("generating interest rubric model=%s", model)
    rubric = await _generate(
        "Generate a candidate-specific interest scoring rubric.",
        _INTEREST_META + preferences,
        client,
        model,
        semaphore,
    )
    cache.put(key, {"rubric": rubric})
    logger.info(
        "generated interest rubric len=%d", len(rubric)
    )
    return rubric


async def generate_fit_rubric(
    resume: str,
    company_name: str,
    company_context: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    semaphore: asyncio.Semaphore,
) -> str:
    key = _cache_key(
        _FIT_META + resume + company_name + company_context
    )
    cached = cache.get(key)
    if cached is not None:
        logger.info(
            "fit rubric cached company=%s", company_name
        )
        return cached["rubric"]

    co_section = (
        "## Company Context\n\n" + company_context
        if company_context
        else ""
    )

    meta = _FIT_META.format(
        month=date.today().strftime("%B %Y"),
        company=company_name,
        company_context_section=co_section,
    )

    logger.info(
        "generating fit rubric company=%s model=%s",
        company_name,
        model,
    )
    rubric = await _generate(
        "Generate a company-specific fit scoring rubric.",
        meta + resume,
        client,
        model,
        semaphore,
    )
    cache.put(key, {"rubric": rubric})
    logger.info(
        "generated fit rubric company=%s len=%d",
        company_name,
        len(rubric),
    )
    return rubric


async def generate_fit_rubrics(
    resume: str,
    companies: dict[str, str],
    company_names: set[str],
    client: anthropic.AsyncAnthropic,
    model: str,
    cache: Cache,
    max_concurrent: int = 5,
) -> dict[str, str]:
    """Generate fit rubrics for each unique company.

    Returns:
        Dict mapping canonical company name to rubric text.
    """
    from job_scraper.companies import canonicalize

    semaphore = asyncio.Semaphore(max_concurrent)
    results: dict[str, str] = {}

    async def gen_one(name: str) -> None:
        canonical = canonicalize(name)
        if canonical in results:
            return
        context = companies.get(canonical, "")
        rubric = await generate_fit_rubric(
            resume,
            name,
            context,
            client,
            model,
            cache,
            semaphore,
        )
        results[canonical] = rubric

    await asyncio.gather(*(
        gen_one(name) for name in company_names
    ))

    logger.info(
        "fit rubrics generated=%d", len(results)
    )
    return results
