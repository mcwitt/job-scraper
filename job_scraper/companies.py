import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def canonicalize(name: str) -> str:
    """Canonicalize a company name to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_companies(companies_dir: Path) -> dict[str, str]:
    """Load company context files. Returns canonical_name -> content."""
    if not companies_dir.is_dir():
        logger.info("no companies dir path=%s", companies_dir)
        return {}
    ctx: dict[str, str] = {}
    for p in sorted(companies_dir.glob("*.md")):
        # resolve symlinks so aliases map to the same content
        ctx[p.stem] = p.read_text().strip()
    logger.info("loaded companies count=%d path=%s", len(ctx), companies_dir)
    return ctx
