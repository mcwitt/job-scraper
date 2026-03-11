import csv
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Connection:
    name: str
    url: str


@dataclass(frozen=True)
class SecondDegree:
    via: Connection
    connections: list[Connection]


LookupFn = Callable[[str], tuple[list[Connection], list[SecondDegree]]]


def _normalize(company: str) -> str:
    return company.strip().lower()


def _parse_connections_csv(path: Path) -> dict[str, list[Connection]]:
    """Parse a LinkedIn connections CSV into {normalized_company: [Connection]}."""
    index: dict[str, list[Connection]] = defaultdict(list)
    text = path.read_text(encoding="utf-8-sig")
    # Skip the notes preamble — find the header row starting with "First Name"
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("First Name,"):
            start = i
            break
    reader = csv.DictReader(lines[start:])
    for row in reader:
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        url = (row.get("URL") or "").strip()
        company = (row.get("Company") or "").strip()
        if not first or not company or not url:
            continue
        name = f"{first}\u00a0{last}".strip()
        index[_normalize(company)].append(Connection(name=name, url=url))
    return dict(index)


def load(linkedin_dir: str | Path) -> LookupFn:
    """Load LinkedIn data and return a lookup(company) closure."""
    linkedin_dir = Path(linkedin_dir)

    # First-degree connections
    first_csv = linkedin_dir / "Connections.csv"
    first_index: dict[str, list[Connection]] = {}
    if first_csv.exists():
        first_index = _parse_connections_csv(first_csv)

    # Second-degree connections grouped by intermediary
    # {normalized_company: [(intermediary, [connections])]}
    second_raw: dict[str, list[tuple[Connection, list[Connection]]]] = (
        defaultdict(list)
    )
    network_dir = linkedin_dir / "network"
    if network_dir.is_dir():
        for person_dir in sorted(network_dir.iterdir()):
            if not person_dir.is_dir():
                continue
            conn_csv = person_dir / "Connections.csv"
            if not conn_csv.exists():
                continue
            info_path = person_dir / "Person.json"
            if info_path.exists():
                info = json.loads(info_path.read_text())
                first_name = info.get("first_name", "")
                last_name = info.get("last_name", "")
                name = f"{first_name}\u00a0{last_name}".strip()
                url = info.get("linkedin_profile_url", "")
                via = Connection(name=name, url=url)
            else:
                via = Connection(name=person_dir.name, url="")
            for company, conns in _parse_connections_csv(conn_csv).items():
                second_raw[company].append((via, conns))

    # Build first-degree URL set for excluding from second-degree
    first_urls: set[str] = set()
    for conns in first_index.values():
        for c in conns:
            first_urls.add(c.url)

    # Build deduplicated second-degree index
    second_index: dict[str, list[SecondDegree]] = {}
    for company, groups in second_raw.items():
        seen: set[str] = set()
        entries: list[SecondDegree] = []
        for via, conns in groups:
            filtered = []
            for c in conns:
                if c.url not in first_urls and c.url not in seen:
                    seen.add(c.url)
                    filtered.append(c)
            if filtered:
                entries.append(SecondDegree(via=via, connections=filtered))
        if entries:
            second_index[company] = entries

    def lookup(company: str) -> tuple[list[Connection], list[SecondDegree]]:
        key = _normalize(company)
        first = first_index.get(key, [])
        second = second_index.get(key, [])
        return first, second

    return lookup
