import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import scipy.sparse as sp
from scipy.stats import spearmanr
from sklearn.ensemble import BaggingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge, RidgeCV
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import cross_val_predict

from job_scraper.comp import format_compensation
from job_scraper.models import Job

Predict = Callable[..., np.ndarray]

logger = logging.getLogger(__name__)


class Vectorizer:
    """Separate TF-IDF for metadata and description."""

    def __init__(self, meta_features: int = 700, desc_features: int = 1300) -> None:
        tok = r"(?u)\b\w+\b"
        kw: dict[str, Any] = dict(
            ngram_range=(1, 3),
            sublinear_tf=True,
            stop_words="english",
            token_pattern=tok,
        )
        self._meta = TfidfVectorizer(max_features=meta_features, **kw)
        self._desc = TfidfVectorizer(max_features=desc_features, **kw)

    def fit_transform(self, metas: list[str], descs: list[str]):
        return sp.hstack(
            [
                self._meta.fit_transform(metas),
                self._desc.fit_transform(descs),
            ]
        )

    def transform(self, metas: list[str], descs: list[str]):
        return sp.hstack(
            [
                self._meta.transform(metas),
                self._desc.transform(descs),
            ]
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


def job_to_example(job: Job, interest_score: int, fit_score: int) -> Example:
    return Example(
        hash=job.hash,
        title=job.title,
        description=job.description,
        company=job.company,
        location=job.location,
        team=job.team,
        comp=format_compensation(job.compensation) if job.compensation else None,
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
                "prep fingerprint changed, discarding training data path=%s",
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
                f.write(json.dumps({"_fingerprint": self._fingerprint}) + "\n")
            for ex in new:
                f.write(json.dumps(asdict(ex)) + "\n")
        self.examples.extend(new)
        self.scored_hashes.update(ex.hash for ex in new)
        logger.info(
            "appended training examples count=%d path=%s",
            len(new),
            self._path,
        )


def _meta(obj: Job | Example) -> str:
    parts = [obj.title, obj.company]
    if obj.location:
        parts.append(obj.location)
    if obj.team:
        parts.append(obj.team)
    comp: str | None
    if isinstance(obj, Job):
        comp = (
            format_compensation(obj.compensation)
            if obj.compensation
            else None
        )
    else:
        comp = obj.comp
    if comp:
        parts.append(comp)
    return " ".join(parts)


def _desc(obj: Job | Example) -> str:
    return obj.description[:16000]


def _texts(
    objs: list[Job] | list[Example],
) -> tuple[list[str], list[str]]:
    return [_meta(o) for o in objs], [_desc(o) for o in objs]


@dataclass(frozen=True)
class Metrics:
    n_examples: int
    cv_r2: float
    cv_mae: float
    cv_spearman: float


def _cv_metrics(X,targets: np.ndarray, alpha: float) -> Metrics:
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
    preds = cross_val_predict(Ridge(alpha=alpha), X, targets, cv=n_folds)

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
    metas = [_meta(j) for j in jobs] + [reference_text]
    descs = [_desc(j) for j in jobs] + [""]
    vec = Vectorizer()
    X = vec.fit_transform(metas, descs)
    sims = cosine_similarity(X[:-1], X[-1:]).flatten()  # type: ignore[arg-type]
    indices = np.argsort(sims)[::-1][:n]
    selected = [jobs[i] for i in indices]
    logger.info(
        "seed_by_similarity jobs=%d ref_len=%d best=%.4f cutoff=%.4f",
        len(jobs),
        len(reference_text),
        float(sims[indices[0]]) if len(indices) > 0 else 0.0,
        float(sims[indices[-1]]) if len(indices) > 0 else 0.0,
    )
    return selected


def _fit_ridge(X,targets: np.ndarray) -> RidgeCV:
    """Pick alpha via LOO-CV, return fitted model."""
    rcv = RidgeCV(alphas=np.logspace(-2, 2, 50))
    rcv.fit(X, targets)
    return rcv


def _dual_head(interest: RidgeCV, fit: RidgeCV) -> Predict:
    """Predict interest and fit separately, combine via geometric mean."""

    def predict(X) -> np.ndarray:
        ip = np.clip(interest.predict(X), 0, 1)
        fp = np.clip(fit.predict(X), 0, 1)
        return np.sqrt(ip * fp)

    return predict


def train(
    examples: list[Example],
    n_models: int = 10,
) -> tuple[Vectorizer, Predict, list[Ridge], Metrics]:
    metas, descs = _texts(examples)
    interest = np.array([ex.interest_score / 100 for ex in examples])
    fit = np.array([ex.fit_score / 100 for ex in examples])
    combined = np.sqrt(interest * fit)

    vectorizer = Vectorizer()
    X = vectorizer.fit_transform(metas, descs)

    model_i = _fit_ridge(X, interest)
    model_f = _fit_ridge(X, fit)
    model = _dual_head(model_i, model_f)

    logger.info(
        "trained surrogate examples=%d features=%d alpha_i=%.4f alpha_f=%.4f",
        len(examples),
        X.shape[1],  # type: ignore[index]
        model_i.alpha_,
        model_f.alpha_,
    )

    bag = BaggingRegressor(
        estimator=Ridge(alpha=1.0),
        n_estimators=n_models,
        bootstrap=True,
    )
    bag.fit(X, combined)  # type: ignore[arg-type]
    ensemble: list[Ridge] = bag.estimators_  # type: ignore[assignment]

    metrics = _cv_metrics(X, combined, 1.0)
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
        float(scores[indices[0]]) if len(indices) > 0 else 0.0,
        float(scores[indices[-1]]) if len(indices) > 0 else 0.0,
    )
    return [jobs[i] for i in indices]


def select_by_disagreement(
    vectorizer: Vectorizer,
    ensemble: list[Ridge],
    jobs: list[Job],
    n: int,
) -> list[Job]:
    """Select jobs where ensemble models disagree most."""
    X = vectorizer.transform(*_texts(jobs))
    preds = np.array([m.predict(X) for m in ensemble])  # type: ignore[arg-type]
    variance = preds.var(axis=0)
    return _select_top_n(jobs, variance, n, "select_by_disagreement")


def select_by_score(
    vectorizer: Vectorizer,
    model: Predict,
    jobs: list[Job],
    n: int,
) -> list[Job]:
    """Select jobs with highest predicted scores."""
    X = vectorizer.transform(*_texts(jobs))
    preds = model(X)
    return _select_top_n(jobs, preds, n, "select_by_score")


def rank_agreement(actual: list[float], predicted: list[float]) -> float | None:
    """Spearman rank correlation, or None if too few pairs."""
    if len(actual) < 3:
        return None
    return round(float(spearmanr(actual, predicted).statistic), 4)


def predict(
    model: tuple[Vectorizer, Predict],
    jobs: list[Job],
) -> list[float]:
    vectorizer, pred = model
    X = vectorizer.transform(*_texts(jobs))
    return pred(X).tolist()
