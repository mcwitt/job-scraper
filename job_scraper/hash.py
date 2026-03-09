import hashlib


def job_hash(title: str, company: str, description: str) -> str:
    text = f"{title}\n{company}\n{description}".lower().strip()
    return hashlib.sha256(text.encode()).hexdigest()
