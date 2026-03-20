import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy.sparse import spmatrix
from scipy.stats import spearmanr
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.model_selection import cross_val_predict

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


@dataclass(frozen=True)
class Metrics:
    n_examples: int
    cv_r2: float
    cv_mae: float
    cv_spearman: float


def _evaluate(
    X: spmatrix, targets: np.ndarray
) -> Metrics:
    """Cross-validated evaluation on pre-computed features."""
    n = len(targets)
    if n < 2:
        logger.warning("too few examples for CV: %d", n)
        return Metrics(
            n_examples=n,
            cv_r2=0.0,
            cv_mae=0.0,
            cv_spearman=0.0,
        )

    n_folds = min(5, n)
    preds = cross_val_predict(
        Ridge(alpha=1.0), X, targets, cv=n_folds
    )

    ss_res = float(np.sum((targets - preds) ** 2))
    ss_tot = float(np.sum((targets - targets.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(targets - preds)))
    rho = float(spearmanr(targets, preds).statistic)

    metrics = Metrics(
        n_examples=n,
        cv_r2=round(r2, 4),
        cv_mae=round(mae, 4),
        cv_spearman=round(rho, 4),
    )
    logger.info(
        "surrogate CV n=%d r2=%.4f mae=%.4f spearman=%.4f",
        metrics.n_examples,
        metrics.cv_r2,
        metrics.cv_mae,
        metrics.cv_spearman,
    )
    return metrics


def train(
    examples: list[Example],
) -> tuple[TfidfVectorizer, Ridge, Metrics]:
    texts = [_text(ex) for ex in examples]
    targets = np.array([
        ex.interest_score * ex.fit_score
        for ex in examples
    ])
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
    metrics = _evaluate(X, targets)
    return vectorizer, model, metrics


def rank_agreement(
    actual: list[float], predicted: list[float]
) -> float | None:
    """Spearman rank correlation, or None if too few pairs."""
    if len(actual) < 3:
        return None
    return round(float(spearmanr(actual, predicted).statistic), 4)


def predict(
    model: tuple[TfidfVectorizer, Ridge],
    jobs: list[Job],
) -> list[float]:
    vectorizer, ridge = model
    texts = [_text(j) for j in jobs]
    X = vectorizer.transform(texts)
    return ridge.predict(X).tolist()
