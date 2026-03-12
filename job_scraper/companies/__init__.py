import logging
import re
from importlib.resources import files

logger = logging.getLogger(__name__)


def canonicalize(name: str) -> str:
    """Canonicalize a company name to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_companies() -> dict[str, str]:
    """Load bundled company context files. Returns canonical_name -> content."""
    ctx: dict[str, str] = {}
    package = files(__package__)
    for item in package.iterdir():
        if item.name.endswith(".md"):
            stem = item.name.removesuffix(".md")
            ctx[stem] = item.read_text(encoding="utf-8").strip()
    logger.info("loaded companies count=%d", len(ctx))
    return ctx
