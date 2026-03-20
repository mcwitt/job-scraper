import hashlib
import json
import logging
import pickle
import random
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import BayesianRidge

from job_scraper.models import Job

logger = logging.getLogger(__name__)


# -- Training data --


def _training_record(
    job: Job,
    interest: float,
    fit: float,
) -> dict:
    """Build a training record with separate fields."""
    return {
        "title": job.title,
        "company": job.company,
        "team": job.team,
        "location": job.location,
        "description": job.description,
        "interest": round(interest, 4),
        "fit": round(fit, 4),
    }


def _record_text(rec: dict) -> str:
    """Concatenate fields for TF-IDF vectorization."""
    parts = [rec["title"], rec["company"]]
    if rec.get("team"):
        parts.append(rec["team"])
    if rec.get("location"):
        parts.append(rec["location"])
    parts.append(rec["description"])
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
    records: list[dict],
) -> tuple[
    TfidfVectorizer,
    BayesianRidge,
    BayesianRidge,
]:
    """Fit TF-IDF + BayesianRidge for interest and fit."""
    texts = [_record_text(r) for r in records]
    interest_targets = [r["interest"] for r in records]
    fit_targets = [r["fit"] for r in records]

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=2,
        max_features=5_000,
        sublinear_tf=True,
        stop_words="english",
    )
    X_sparse: Any = vectorizer.fit_transform(texts)
    X = X_sparse.toarray()

    interest_model = BayesianRidge()
    interest_model.fit(X, interest_targets)

    fit_model = BayesianRidge()
    fit_model.fit(X, fit_targets)

    return vectorizer, interest_model, fit_model


def predict(
    vectorizer: TfidfVectorizer,
    interest_model: BayesianRidge,
    fit_model: BayesianRidge,
    jobs: list[Job],
) -> list[tuple[Job, float, float, float, float]]:
    """Predict interest + fit for jobs.

    Returns (job, combined, interest, fit, uncertainty)
    sorted descending by combined score.
    """
    if not jobs:
        return []
    texts = [job_text(j) for j in jobs]
    X_sparse: Any = vectorizer.transform(texts)
    X = X_sparse.toarray()

    i_pred: Any = interest_model.predict(
        X, return_std=True
    )
    i_mean, i_std = i_pred
    f_pred: Any = fit_model.predict(
        X, return_std=True
    )
    f_mean, f_std = f_pred

    results = []
    for idx, job in enumerate(jobs):
        im = float(i_mean[idx])
        fs = float(f_mean[idx])
        combined = (max(im, 0) * max(fs, 0)) ** 0.5
        unc = float(i_std[idx] + f_std[idx]) / 2
        results.append((job, combined, im, fs, unc))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# -- Selection (active learning) --


def select_for_scoring(
    ranked: list[tuple[Job, float, float, float, float]],
    budget: int,
    explore_frac: float = 0.1,
    uncertain_frac: float = 0.1,
) -> list[Job]:
    """Select jobs for LLM scoring: exploit + explore.

    Allocates budget across three strategies:
    - exploit: top by predicted combined score
    - explore: uniform random from remaining
    - uncertain: highest uncertainty from remaining
    """
    if not ranked or budget <= 0:
        return []

    n_exploit = max(
        1,
        budget
        - int(budget * explore_frac)
        - int(budget * uncertain_frac),
    )
    n_explore = int(budget * explore_frac)
    n_uncertain = budget - n_exploit - n_explore

    # Exploit: top-k by combined score
    exploit = [j for j, *_ in ranked[:n_exploit]]
    remaining = ranked[n_exploit:]

    if not remaining:
        return exploit

    # Uncertain: highest uncertainty from remaining
    by_unc = sorted(
        remaining, key=lambda x: x[4], reverse=True
    )
    uncertain = [j for j, *_ in by_unc[:n_uncertain]]
    uncertain_set = {j.hash for j in uncertain}

    # Explore: random from remaining
    rest = [
        r for r in remaining if r[0].hash not in uncertain_set
    ]
    rng = random.Random(42)  # noqa: S311
    k = min(n_explore, len(rest))
    explore = [r[0] for r in rng.sample(rest, k)]

    selected = exploit + uncertain + explore
    logger.info(
        "selection exploit=%d uncertain=%d explore=%d"
        " total=%d",
        len(exploit),
        len(uncertain),
        len(explore),
        len(selected),
    )
    return selected


# -- Persistence --


def save(
    path: Path,
    vectorizer: TfidfVectorizer,
    interest_model: BayesianRidge,
    fit_model: BayesianRidge,
    training_data: list[dict],
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
            f.write(json.dumps(entry) + "\n")
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
        BayesianRidge,
        BayesianRidge,
        list[dict],
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

    training_data: list[dict] = []
    with data_path.open() as f:
        for line in f:
            training_data.append(json.loads(line))

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
    existing: list[dict],
) -> list[dict]:
    """Merge new LLM scores into training data, dedup."""
    records = list(existing)
    for job in jobs:
        i = interest_scores.get(job.hash)
        f = fit_scores.get(job.hash)
        if i is not None and f is not None:
            records.append(
                _training_record(
                    job,
                    float(i["score"]),
                    float(f["score"]),
                )
            )

    # Dedup by text (keep latest)
    seen: dict[str, dict] = {}
    for rec in records:
        seen[_record_text(rec)] = rec
    return list(seen.values())


def sample(jobs: list[Job], n: int) -> list[Job]:
    """Uniform random sample for cold start."""
    k = min(n, len(jobs))
    rng = random.Random(42)  # noqa: S311
    return rng.sample(jobs, k)


# -- Evaluation --


def evaluate(
    ranked: list[tuple[Job, float, float, float, float]],
    interest_scores: dict,
    fit_scores: dict,
) -> None:
    """Log surrogate accuracy vs LLM scores."""
    from scipy.stats import spearmanr

    pairs: list[tuple[float, float]] = []
    for job, combined, _, _, _ in ranked:
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
        abs(p - a) for p, a in zip(preds, actuals, strict=True)
    ) / len(pairs)
    rho, _ = spearmanr(preds, actuals)

    logger.info(
        "surrogate eval n=%d mae=%.4f spearman=%.4f",
        len(pairs),
        mae,
        rho,
    )
