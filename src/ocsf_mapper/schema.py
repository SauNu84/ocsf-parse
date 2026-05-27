"""Loader for the vendored OCSF schema.

The OCSF schema lives as a git submodule at ``<repo>/ocsf-schema/``. This module
finds it, parses the JSON files, and exposes helpers used by the validator,
the generator, and the linter:

  * :meth:`Schema.load_class`     — class definition, merged with extends chain
  * :meth:`Schema.dictionary`     — full attribute dictionary
  * :meth:`Schema.categories`     — categories.json
  * :meth:`Schema.class_summaries`— short index of every event class
  * :meth:`Schema.version`        — version string declared by the schema

Schema location resolution order:

  1. Explicit ``root=`` argument to :class:`Schema`
  2. ``OCSF_SCHEMA_ROOT`` environment variable
  3. ``ocsf-schema/`` directory next to the repository root (the submodule)
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


# repo-relative default: src/ocsf_mapper/schema.py -> ../../ocsf-schema
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "ocsf-schema"


def default_schema_root() -> Path:
    """Resolve the default schema location (env var > submodule)."""
    env = os.environ.get("OCSF_SCHEMA_ROOT")
    if env:
        return Path(env)
    return _DEFAULT_ROOT


class Schema:
    """Thin reader over a checked-out copy of the ocsf-schema repo."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else default_schema_root()
        if not self.root.is_dir():
            raise FileNotFoundError(
                f"OCSF schema not found at {self.root}. "
                "Run `git submodule update --init --recursive` or set OCSF_SCHEMA_ROOT."
            )

    # -- raw file loaders -------------------------------------------------

    def _load(self, rel: str) -> Any:
        return json.loads((self.root / rel).read_text())

    def version(self) -> str:
        return self._load("version.json")["version"]

    def categories(self) -> dict:
        return self._load("categories.json")

    def dictionary(self) -> dict:
        return self._load("dictionary.json")

    # -- class lookup -----------------------------------------------------

    def load_class(self, name: str) -> dict:
        """Load an event class by name, with attributes/constraints merged up the extends chain."""
        return _load_class_cached(self.root, name)

    def class_summaries(self) -> list[dict]:
        """One row per event class: {name, caption, category, description, path}."""
        out = []
        for p in (self.root / "events").rglob("*.json"):
            if p.name == "base_event.json":
                continue
            cls = json.loads(p.read_text())
            if "caption" not in cls or "name" not in cls:
                continue
            out.append(
                {
                    "name": cls["name"],
                    "caption": cls["caption"],
                    "category": cls.get("category") or p.parent.name,
                    "description": cls.get("description", ""),
                    "path": str(p.relative_to(self.root)),
                }
            )
        return sorted(out, key=lambda x: (x["category"], x["name"]))


@lru_cache(maxsize=None)
def _load_class_cached(root: Path, name: str) -> dict:
    """Merge a class definition with its ancestors (extends chain)."""
    candidates = list((root / "events").rglob(f"{name}.json"))
    if not candidates:
        if name == "base_event":
            return json.loads((root / "events" / "base_event.json").read_text())
        raise FileNotFoundError(f"OCSF class not found: {name}")
    cls = json.loads(candidates[0].read_text())
    if "extends" in cls and cls["extends"] != cls["name"]:
        parent = _load_class_cached(root, cls["extends"])
        merged_attrs = {**parent.get("attributes", {}), **cls.get("attributes", {})}
        merged_constraints = {
            **parent.get("constraints", {}),
            **cls.get("constraints", {}),
        }
        cls = {
            **parent,
            **cls,
            "attributes": merged_attrs,
            "constraints": merged_constraints,
        }
    return cls
