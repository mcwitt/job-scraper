import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy.sparse import spmatrix
from scipy.stats import spearmanr
from sklearn.ensemble import BaggingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge, RidgeCV
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import cross_val_predict

from job_scraper.models import Job

logger = logging.getLogger(__name__)


_ALPHAS = np.logspace(-2, 2, 20)  # 0.01 to 100, 20 points


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
    interest_score: int
    fit_score: int


def job_to_example(
    job: Job, interest_score: int, fit_score: int
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


class TrainingData:
    """Fingerprint-gated training data store.

    If the prep artifacts change (rubric/brief updated),
    existing examples are discarded automatically.
    """

    def __init__(
        self,
        path: Path,
        interest_rubric: str,
        candidate_brief: str,
    ) -> None:
        self._path = path
        self._fingerprint = hashlib.sha256(
            (interest_rubric + "\n" + candidate_brief).encode()
        ).hexdigest()[:16]
        self.examples: list[Example] = []
        self.scored_hashes: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            f = self._path.open()
        except FileNotFoundError:
            return
        with f:
            lines = f.read().splitlines()
        if not lines:
            return
        header = json.loads(lines[0])
        if header.get("_fingerprint") != self._fingerprint:
            logger.info(
                "prep fingerprint changed, discarding"
                " training data path=%s",
                self._path,
            )
            self._path.unlink(missing_ok=True)
            return
        for line in lines[1:]:
            ex = Example(**json.loads(line))
            self.examples.append(ex)
            self.scored_hashes.add(ex.hash)
        logger.info(
            "loaded training examples count=%d path=%s",
            len(self.examples),
            self._path,
        )

    def append(self, new: list[Example]) -> None:
        if not new:
            return
        write_header = not self._path.exists()
        with self._path.open("a") as f:
            if write_header:
                f.write(
                    json.dumps(
                        {"_fingerprint": self._fingerprint}
                    )
                    + "\n"
                )
            for ex in new:
                f.write(json.dumps(asdict(ex)) + "\n")
        self.examples.extend(new)
        self.scored_hashes.update(ex.hash for ex in new)
        logger.info(
            "appended training examples count=%d path=%s",
            len(new),
            self._path,
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


def _cv_metrics(
    X: spmatrix, targets: np.ndarray, alpha: float
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
        Ridge(alpha=alpha), X, targets, cv=n_folds
    )

    ss_res = float(np.sum((targets - preds) ** 2))
    ss_tot = float(np.sum((targets - targets.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(targets - preds)))
    rho = float(spearmanr(targets, preds).statistic)

    return Metrics(
        n_examples=n,
        cv_r2=round(r2, 4),
        cv_mae=round(mae, 4),
        cv_spearman=round(rho, 4),
    )


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
        (ex.interest_score / 100) * (ex.fit_score / 100)
        for ex in examples
    ])
    vectorizer = _make_vectorizer()
    X = vectorizer.fit_transform(texts)

    # Pick alpha via built-in LOO cross-validation
    rcv = RidgeCV(alphas=_ALPHAS)
    rcv.fit(X, targets)  # type: ignore[arg-type]
    alpha = float(rcv.alpha_)

    model = Ridge(alpha=alpha)
    model.fit(X, targets)
    logger.info(
        "trained surrogate examples=%d features=%d alpha=%.4f",
        len(examples),
        len(vectorizer.get_feature_names_out()),
        alpha,
    )

    # Bootstrap ensemble for uncertainty estimation
    bag = BaggingRegressor(
        estimator=Ridge(alpha=alpha),
        n_estimators=n_models,
        bootstrap=True,
    )
    bag.fit(X, targets)
    ensemble: list[Ridge] = bag.estimators_  # type: ignore[assignment]

    metrics = _cv_metrics(X, targets, alpha)
    logger.info(
        "surrogate CV r2=%.4f mae=%.4f spearman=%.4f",
        metrics.cv_r2,
        metrics.cv_mae,
        metrics.cv_spearman,
    )
    return vectorizer, model, ensemble, metrics


def _select_top_n(
    jobs: list[Job],
    scores: np.ndarray,
    n: int,
    label: str,
) -> list[Job]:
    """Select jobs with highest values in scores array."""
    indices = np.argsort(scores)[::-1][:n]
    logger.info(
        "%s jobs=%d n=%d best=%.6f cutoff=%.6f",
        label,
        len(jobs),
        n,
        float(scores[indices[0]])
        if len(indices) > 0
        else 0.0,
        float(scores[indices[-1]])
        if len(indices) > 0
        else 0.0,
    )
    return [jobs[i] for i in indices]


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
    return _select_top_n(
        jobs, variance, n, "select_by_disagreement"
    )


def select_by_score(
    vectorizer: TfidfVectorizer,
    model: Ridge,
    jobs: list[Job],
    n: int,
) -> list[Job]:
    """Select jobs with highest predicted scores."""
    texts = [_text(j) for j in jobs]
    X = vectorizer.transform(texts)
    preds = model.predict(X)
    return _select_top_n(
        jobs, preds, n, "select_by_score"
    )


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
