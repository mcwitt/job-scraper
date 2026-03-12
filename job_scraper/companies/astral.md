# Astral

## Overview

Developer tools company building high-performance Python tooling in
Rust. Makes Ruff (linter/formatter), uv (package/project manager),
and ty (type checker/language server). Tools are 10-100x faster than
existing Python equivalents. Adopted by major projects (Airflow,
FastAPI, Pandas, SciPy) and companies (Amazon, Microsoft, Netflix,
Hugging Face). Founded by Charlie Marsh.

## Technical Focus

- Ruff: Python linter and formatter (10-100x faster than flake8,
  >99.9% Black-compatible formatting)
- uv: Python package installer, resolver, and project manager
  (drop-in pip/pip-tools replacement)
- ty: Python type checker and language server (10-60x faster than
  mypy/Pyright, 500x faster incremental updates)
- All tools written in Rust for performance
- Incremental computation architecture for editor-speed feedback

## Scale & Stage

Private, Seed. Raised $4M seed led by Accel in 2023. ~26 employees.
Small distributed team including authors of ripgrep, bat, hyperfine,
and maturin; early Biome contributors; and CPython core developers.
No reported Series A as of early 2026.

## Hiring

Very small team with deep expertise in Rust, compilers, and
developer tooling. Early hires came from the open-source ecosystem
(ripgrep, CPython, Biome, Prefect). Expect a high bar for systems
programming and language tooling experience. Open-source-first
culture with all major products on GitHub.

## Recent Context

Released ty Beta in December 2025 -- a Rust-based Python type
checker achieving 10-60x speedups over mypy/Pyright and 500x faster
incremental rechecks. Targeting stable ty release in 2026 with >60%
typing spec conformance and Pydantic/Django support. Runs the Astral
OSS Fund, donating to Python ecosystem projects ($70K in year one,
$44K pledged for year two).
