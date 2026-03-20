import hashlib
import json
import logging
import pickle
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge

from job_scraper.models import Job

logger = logging.getLogger(__name__)

SCORE_PRECISION = 4


# -- Training data --


@dataclass(frozen=True)
class TrainingRecord:
    title: str
    company: str
    description: str
    interest: float
    fit: float
    team: str | None = None
    location: str | None = None

    @classmethod
    def from_job(
        cls, job: Job, interest: float, fit: float
    ) -> "TrainingRecord":
        return cls(
            title=job.title,
            company=job.company,
            team=job.team,
            location=job.location,
            description=job.description,
            interest=round(interest, SCORE_PRECISION),
            fit=round(fit, SCORE_PRECISION),
        )

    def text(self) -> str:
        """Concatenate fields for TF-IDF."""
        parts = [self.title, self.company]
        if self.team:
            parts.append(self.team)
        if self.location:
            parts.append(self.location)
        parts.append(self.description)
        return " ".join(parts)


def job_text(job: Job) -> str:
    """Concatenate job fields for vectorization."""
    parts = [job.title, job.company]
    if job.team:
        parts.append(job.team)
    if job.location:
        parts.append(job.location)
    parts.append(job.description)
    return " ".join(parts)


# -- Model --


def config_hash(preferences: str, resume: str) -> str:
    """Hash preferences + resume for change detection."""
    raw = preferences + "\n" + resume
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def train(
    records: list[TrainingRecord],
) -> tuple[TfidfVectorizer, Ridge, Ridge]:
    """Fit TF-IDF + Ridge for interest and fit."""
    texts = [r.text() for r in records]
    interest_targets = [r.interest for r in records]
    fit_targets = [r.fit for r in records]

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=2,
        max_features=5_000,
        sublinear_tf=True,
        stop_words="english",
    )
    X = vectorizer.fit_transform(texts)

    interest_model = Ridge()
    interest_model.fit(X, interest_targets)

    fit_model = Ridge()
    fit_model.fit(X, fit_targets)

    return vectorizer, interest_model, fit_model


def predict(
    vectorizer: TfidfVectorizer,
    interest_model: Ridge,
    fit_model: Ridge,
    jobs: list[Job],
) -> list[tuple[Job, float, float, float]]:
    """Predict interest + fit for jobs.

    Returns (job, combined, interest, fit)
    sorted descending by combined score.
    """
    if not jobs:
        return []
    texts = [job_text(j) for j in jobs]
    X = vectorizer.transform(texts)

    i_pred = interest_model.predict(X)
    f_pred = fit_model.predict(X)

    results = []
    for idx, job in enumerate(jobs):
        im = float(i_pred[idx])
        fs = float(f_pred[idx])
        combined = (max(im, 0) * max(fs, 0)) ** 0.5
        results.append((job, combined, im, fs))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# -- Exploration --


def select_explore(
    ranked: list[tuple[Job, float, float, float]],
    top_k: int,
    budget: int,
) -> list[Job]:
    """Random sample from jobs outside top-k."""
    remaining = ranked[top_k:]
    if not remaining or budget <= 0:
        return []
    k = min(budget, len(remaining))
    selected = [r[0] for r in random.sample(remaining, k)]
    logger.info("explore sample=%d", len(selected))
    return selected


# -- Persistence --


def save(
    path: Path,
    vectorizer: TfidfVectorizer,
    interest_model: Ridge,
    fit_model: Ridge,
    training_data: list[TrainingRecord],
    cfg_hash: str,
) -> None:
    """Persist surrogate models + training data."""
    path.mkdir(parents=True, exist_ok=True)
    with (path / "model.pkl").open("wb") as f:
        pickle.dump(
            (vectorizer, interest_model, fit_model), f
        )
    with (path / "training_data.jsonl").open("w") as f:
        for entry in training_data:
            f.write(json.dumps(asdict(entry)) + "\n")
    with (path / "meta.json").open("w") as f:
        json.dump(
            {
                "config_hash": cfg_hash,
                "n_samples": len(training_data),
            },
            f,
        )
    logger.info(
        "saved surrogate n_samples=%d path=%s",
        len(training_data),
        path,
    )


def load(
    path: Path, cfg_hash: str
) -> (
    tuple[
        TfidfVectorizer,
        Ridge,
        Ridge,
        list[TrainingRecord],
    ]
    | None
):
    """Load persisted surrogate if it exists and matches."""
    meta_path = path / "meta.json"
    model_path = path / "model.pkl"
    data_path = path / "training_data.jsonl"

    if not all(
        p.exists()
        for p in (meta_path, model_path, data_path)
    ):
        return None

    with meta_path.open() as f:
        meta = json.load(f)

    if meta.get("config_hash") != cfg_hash:
        logger.info(
            "surrogate config changed, discarding"
        )
        return None

    with model_path.open("rb") as f:
        vectorizer, interest_model, fit_model = (
            pickle.load(f)  # noqa: S301
        )

    training_data: list[TrainingRecord] = []
    with data_path.open() as f:
        for line in f:
            training_data.append(
                TrainingRecord(**json.loads(line))
            )

    logger.info(
        "loaded surrogate n_samples=%d path=%s",
        meta.get("n_samples"),
        path,
    )
    return vectorizer, interest_model, fit_model, training_data


# -- Training data management --


def augment_training_data(
    jobs: list[Job],
    interest_scores: dict,
    fit_scores: dict,
    existing: list[TrainingRecord],
) -> list[TrainingRecord]:
    """Merge new LLM scores into training data, dedup."""
    records = list(existing)
    for job in jobs:
        i = interest_scores.get(job.hash)
        f = fit_scores.get(job.hash)
        if i is not None and f is not None:
            records.append(
                TrainingRecord.from_job(
                    job,
                    float(i["score"]),
                    float(f["score"]),
                )
            )

    # Dedup by text (keep latest)
    seen: dict[str, TrainingRecord] = {}
    for rec in records:
        seen[rec.text()] = rec
    return list(seen.values())


def sample(jobs: list[Job], n: int) -> list[Job]:
    """Uniform random sample for cold start."""
    k = min(n, len(jobs))
    rng = random.Random(42)  # noqa: S311
    return rng.sample(jobs, k)


# -- Evaluation --


def evaluate(
    ranked: list[tuple[Job, float, float, float]],
    interest_scores: dict,
    fit_scores: dict,
) -> None:
    """Log surrogate accuracy vs LLM scores."""
    from scipy.stats import spearmanr

    pairs: list[tuple[float, float]] = []
    for job, combined, _, _ in ranked:
        i = interest_scores.get(job.hash)
        f = fit_scores.get(job.hash)
        if i is not None and f is not None:
            actual = (
                float(i["score"]) * float(f["score"])
            ) ** 0.5
            pairs.append((combined, actual))

    if len(pairs) < 2:
        return

    preds, actuals = zip(*pairs, strict=True)
    mae = sum(
        abs(p - a)
        for p, a in zip(preds, actuals, strict=True)
    ) / len(pairs)
    rho, _ = spearmanr(preds, actuals)

    logger.info(
        "surrogate eval n=%d mae=%.4f spearman=%.4f",
        len(pairs),
        mae,
        rho,
    )
