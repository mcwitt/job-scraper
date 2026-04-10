"""Load scrape configuration from TOML."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BoardEntry:
    slug: str
    name: str
    extra: dict[str, str | bool | int] = field(
        default_factory=dict
    )
    cache_ttl: float | None = None


@dataclass(frozen=True)
class CustomScript:
    name: str
    path: str
    cache_ttl: float | None = None


@dataclass(frozen=True)
class CustomCommand:
    name: str
    command: list[str]
    cache_ttl: float | None = None


@dataclass(frozen=True)
class Config:
    boards: dict[str, list[BoardEntry]]
    custom: list[CustomScript | CustomCommand]

    def all_names(self) -> set[str]:
        """All scraper names (board slug or custom name)."""
        names: set[str] = set()
        for entries in self.boards.values():
            for e in entries:
                names.add(e.slug)
        for c in self.custom:
            names.add(c.name)
        return names


def _parse_board_section(
    platform: str, raw: dict,
) -> list[BoardEntry]:
    entries: list[BoardEntry] = []
    for slug, val in raw.items():
        if isinstance(val, str):
            entries.append(BoardEntry(slug=slug, name=val))
        elif isinstance(val, dict):
            name = val["name"]
            ttl = val.get("cache_ttl")
            extra = {
                k: v
                for k, v in val.items()
                if k not in ("name", "cache_ttl")
            }
            entries.append(
                BoardEntry(
                    slug=slug,
                    name=name,
                    extra=extra,
                    cache_ttl=ttl,
                )
            )
        else:
            msg = (
                f"boards.{platform}.{slug}: expected string"
                f" or table, got {type(val).__name__}"
            )
            raise ValueError(msg)
    return entries


def load_config(path: Path) -> Config:
    """Parse a scrape.toml config file."""
    with path.open("rb") as f:
        raw = tomllib.load(f)

    boards: dict[str, list[BoardEntry]] = {}
    for platform, section in raw.get("boards", {}).items():
        boards[platform] = _parse_board_section(
            platform, section
        )

    custom: list[CustomScript | CustomCommand] = []
    for name, section in raw.get("custom", {}).items():
        ttl = section.get("cache_ttl")
        if "path" in section:
            custom.append(
                CustomScript(
                    name=name,
                    path=section["path"],
                    cache_ttl=ttl,
                )
            )
        elif "command" in section:
            custom.append(
                CustomCommand(
                    name=name,
                    command=section["command"],
                    cache_ttl=ttl,
                )
            )
        else:
            msg = (
                f"custom.{name}: must have 'path' or 'command'"
            )
            raise ValueError(msg)

    return Config(boards=boards, custom=custom)
