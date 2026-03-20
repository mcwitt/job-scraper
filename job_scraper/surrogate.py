import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge

from job_scraper.models import Job

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Example:
    hash: str
    title: str
    description: str
    company: str
    location: str | None
    team: str | None
    comp: str | None
    interest_score: float
    fit_score: float


def job_to_example(
    job: Job, interest_score: float, fit_score: float
) -> Example:
    return Example(
        hash=job.hash,
        title=job.title,
        description=job.description,
        company=job.company,
        location=job.location,
        team=job.team,
        comp=job.comp,
        interest_score=interest_score,
        fit_score=fit_score,
    )


def load_examples(path: Path) -> list[Example]:
    try:
        f = path.open()
    except FileNotFoundError:
        return []
    examples: list[Example] = []
    with f:
        for line in f:
            d = json.loads(line)
            examples.append(Example(**d))
    logger.info(
        "loaded training examples count=%d path=%s",
        len(examples),
        path,
    )
    return examples


def append_examples(
    path: Path, examples: list[Example]
) -> None:
    with path.open("a") as f:
        for ex in examples:
            f.write(json.dumps(asdict(ex)) + "\n")
    logger.info(
        "appended training examples count=%d path=%s",
        len(examples),
        path,
    )


def _text(obj: Job | Example) -> str:
    parts = [obj.title, obj.company, obj.description[:4000]]
    if obj.location:
        parts.append(obj.location)
    if obj.team:
        parts.append(obj.team)
    if obj.comp:
        parts.append(obj.comp)
    return " ".join(parts)


def train(
    examples: list[Example],
) -> tuple[TfidfVectorizer, Ridge]:
    texts = [_text(ex) for ex in examples]
    targets = [
        ex.interest_score * ex.fit_score
        for ex in examples
    ]
    vectorizer = TfidfVectorizer(
        max_features=5000, stop_words="english"
    )
    X = vectorizer.fit_transform(texts)
    model = Ridge(alpha=1.0)
    model.fit(X, targets)
    logger.info(
        "trained surrogate examples=%d features=%d",
        len(examples),
        len(vectorizer.get_feature_names_out()),
    )
    return vectorizer, model


def predict(
    model: tuple[TfidfVectorizer, Ridge],
    jobs: list[Job],
) -> list[float]:
    vectorizer, ridge = model
    texts = [_text(j) for j in jobs]
    X = vectorizer.transform(texts)
    return ridge.predict(X).tolist()
