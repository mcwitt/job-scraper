import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy.sparse import spmatrix
from scipy.stats import spearmanr
from sklearn.ensemble import BaggingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import cross_val_predict

from job_scraper.models import Job

logger = logging.getLogger(__name__)


def _make_vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(
        max_features=5000, stop_words="english"
    )


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


def seed_by_similarity(
    jobs: list[Job],
    reference_text: str,
    n: int,
) -> list[Job]:
    """Select jobs most similar to reference text via TF-IDF cosine."""
    texts = [_text(j) for j in jobs]
    texts.append(reference_text)
    vec = _make_vectorizer()
    X = vec.fit_transform(texts)
    sims = cosine_similarity(X[:-1], X[-1:]).flatten()  # type: ignore[index]
    indices = np.argsort(sims)[::-1][:n]
    selected = [jobs[i] for i in indices]
    logger.info(
        "seed_by_similarity jobs=%d ref_len=%d "
        "best=%.4f cutoff=%.4f",
        len(jobs),
        len(reference_text),
        float(sims[indices[0]]) if len(indices) > 0 else 0.0,
        float(sims[indices[-1]]) if len(indices) > 0 else 0.0,
    )
    return selected


def train(
    examples: list[Example],
    n_models: int = 10,
) -> tuple[TfidfVectorizer, Ridge, list[Ridge], Metrics]:
    texts = [_text(ex) for ex in examples]
    targets = np.array([
        ex.interest_score * ex.fit_score
        for ex in examples
    ])
    vectorizer = _make_vectorizer()
    X = vectorizer.fit_transform(texts)
    model = Ridge(alpha=1.0)
    model.fit(X, targets)
    logger.info(
        "trained surrogate examples=%d features=%d",
        len(examples),
        len(vectorizer.get_feature_names_out()),
    )

    # Bootstrap ensemble for uncertainty estimation
    bag = BaggingRegressor(
        estimator=Ridge(alpha=1.0),
        n_estimators=n_models,
        bootstrap=True,
    )
    bag.fit(X, targets)
    ensemble: list[Ridge] = bag.estimators_  # type: ignore[assignment]

    metrics = _evaluate(X, targets)
    return vectorizer, model, ensemble, metrics


def select_by_disagreement(
    vectorizer: TfidfVectorizer,
    ensemble: list[Ridge],
    jobs: list[Job],
    n: int,
) -> list[Job]:
    """Select jobs where ensemble models disagree most."""
    texts = [_text(j) for j in jobs]
    X = vectorizer.transform(texts)
    preds = np.array([m.predict(X) for m in ensemble])
    variance = preds.var(axis=0)
    indices = np.argsort(variance)[::-1][:n]
    logger.info(
        "select_by_disagreement jobs=%d n=%d "
        "max_var=%.6f cutoff_var=%.6f",
        len(jobs),
        n,
        float(variance[indices[0]]) if len(indices) > 0 else 0.0,
        float(variance[indices[-1]])
        if len(indices) > 0
        else 0.0,
    )
    return [jobs[i] for i in indices]


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
