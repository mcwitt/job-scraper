import html

from bs4 import BeautifulSoup


def html_to_text(raw: str) -> str:
    """Convert an HTML string to plain text."""
    unescaped = html.unescape(raw)
    soup = BeautifulSoup(unescaped, "lxml")
    return soup.get_text(separator="\n", strip=True)
