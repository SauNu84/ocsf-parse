# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`ocsf-mapper` is a Python tool that converts raw security log lines into [OCSF](https://schema.ocsf.io/) events via a declarative JSON mapping DSL. It ships as a pip package, Docker image, and Spark UDF. Version: **0.4.4**.

## Commands

```bash
# Install all features for development
pip install -e '.[dev,web,parquet,fast]'

# Run full test suite (90% coverage threshold enforced)
python -m pytest --cov=ocsf_mapper --cov-report=term --cov-fail-under=90

# Run a single test file
python -m pytest tests/test_apply.py -v

# Run a single test by name
python -m pytest tests/test_ops.py -k "test_path_op" -v

# CI lint gate — validates all 43 mappings against their pinned samples
python -m ocsf_mapper.lint mappings/

# Start the web UI locally
ocsf-mapper serve  # → http://127.0.0.1:8000

# Apply a mapping from CLI
ocsf-mapper apply mappings/nginx.json samples/nginx.log

# Generate an LLM-assisted mapping draft (requires ANTHROPIC_API_KEY or OPENAI_API_KEY)
ocsf-mapper generate my_source samples/my_source.log
```

There is no build step — the project is pure Python. Docker: `docker build -t ocsf-mapper .`

## Architecture

### Pipeline

Every log line flows through a single linear pipeline in `apply.py`:

```
Raw log line
  → parse_record()     # JSON / regex / CEF / LEEF auto-detected
  → pick_class()       # routing: match field patterns → OCSF class_uid
  → map_record()       # apply DSL ops per target field
  → prune()            # drop None values
  → OCSF event dict
  → Sink               # jsonl / csv / parquet / security-lake / stdout
```

### Mapping DSL

A mapping is a JSON file in `mappings/`. It defines:
- `parser`: how to parse the raw line (`json`, `regex`, `cef`, `leef`)
- `routing`: ordered list of `{match: {...}, class_uid: N}` rules
- `fields`: list of `{target: "ocsf.field", op: {...}}` entries

`ops.py` implements 11 op kinds dispatched by `op["op"]`:
- `const`, `path`, `lookup`, `time`, `range`, `int`, `bool`, `expr`, `group`, `raw`, `for_each`

`validate.py` checks mapped output against the OCSF JSON schema (loaded from the `ocsf-schema/` git submodule via `schema.py`).

### LLM Generation (`generate.py`)

Two-phase process:
1. **suggest_classes**: send sample + OCSF class list to LLM → ranked class candidates
2. **draft_mapping**: send sample + chosen class schema → full DSL mapping JSON

Provider abstraction in `providers/` supports `AnthropicProvider`, `OpenAIProvider`, and `FixtureProvider` (used in tests to avoid real API calls).

### Web UI (`web/app.py`)

FastAPI + Jinja2 + HTMX. Key routes:
- `GET /` — card grid of all 43 sources + OCSF class tree search
- `GET /sources/{name}` — six HTMX tabs: Sample, Output, Mapping (Monaco editor), Validation, Coverage, Snippets
- `POST /sources/{name}/apply` — drag-drop test: raw log → side-by-side OCSF output + validation errors
- `POST /new` — wizard: upload sample → LLM drafts mapping → save to `mappings/`
- `GET /audit` — last 500 mapping edits with diff

### Output Sinks (`sinks/`)

`JsonlSink`, `CsvSink`, `ParquetSink`, `SecurityLakeSink` (AWS-partitioned Parquet), `RedactingSink` (wraps any sink to strip PII). All implement the same `write(event) / close()` interface.

### Public API (`__init__.py`)

```python
from ocsf_mapper import apply, apply_stream, apply_with_class, apply_stream_with_class
from ocsf_mapper import validate, validate_stream, Schema, list_mappings
```

`apply()` → single event dict. `apply_stream()` → generator over a file/stdin.

## Key Conventions

### Adding a New Mapping

1. Put the JSON file in `mappings/<source_name>.json`
2. Put a representative sample in `samples/<source_name>.log` (or `.json`)
3. Add an entry to `catalog.json` (vendor, priority, OCSF class)
4. Run `python -m ocsf_mapper.lint mappings/` — must pass before merging

### OCSF Schema

The schema lives in `ocsf-schema/` (git submodule pinned to OCSF 1.7.0/1.8.0 worktrees set up by `scripts/setup_schema_versions.sh`). `schema.py` loads it lazily and caches it. Do not edit files inside `ocsf-schema/`.

### Zero Core Dependencies

The package has **no mandatory runtime dependencies**. Optional extras must remain optional — guard imports with `try/except ImportError` and surface a clear message (see `_fastjson.py` and the sink modules for the pattern).

### Tests

- `tests/fixtures/` holds minimal DSL snippets used across multiple test files — prefer extending fixtures over copy-pasting inline dicts.
- LLM-dependent tests use `FixtureProvider` (set via env var `OCSF_PROVIDER=fixture` or monkeypatching) — no real API keys needed in CI.
- Coverage threshold is 90%; new modules need tests to land.

### CI

`.github/workflows/ci.yml` runs on Python 3.9, 3.11, 3.12. It checks out submodules, pins schema versions, installs `[dev,web,parquet,fast]`, runs pytest, then runs lint. Both must pass.

### Releases

Triggered by `git tag v<version>`. `publish.yml` sanity-checks that the tag version matches both `pyproject.toml` and `src/ocsf_mapper/__init__.py` before publishing to PyPI via OIDC Trusted Publisher and pushing to GHCR.
