"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ocsf_mapper.schema import Schema

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def schema() -> Schema:
    """The vendored OCSF schema. Shared across the suite to keep tests fast."""
    return Schema()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def mappings_dir(repo_root: Path) -> Path:
    return repo_root / "mappings"


@pytest.fixture(scope="session")
def samples_dir(repo_root: Path) -> Path:
    return repo_root / "samples"


@pytest.fixture
def cloudtrail_config(mappings_dir: Path) -> dict:
    return json.loads((mappings_dir / "cloudtrail.json").read_text())


@pytest.fixture
def cloudtrail_lines(samples_dir: Path) -> list[str]:
    return (samples_dir / "cloudtrail.jsonl").read_text().splitlines()
