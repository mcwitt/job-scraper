import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def canonicalize(name: str) -> str:
    """Canonicalize a company name to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_companies(path: Path) -> dict[str, str]:
    """Load company context files from a directory.

    Returns canonical_name -> content.
    """
    ctx: dict[str, str] = {}
    try:
        items = sorted(path.glob("*.md"))
    except OSError:
        logger.warning(
            "companies directory not found path=%s", path
        )
        return ctx
    for item in items:
        stem = item.name.removesuffix(".md")
        ctx[stem] = item.read_text(encoding="utf-8").strip()
    logger.info("loaded companies count=%d", len(ctx))
    return ctx
