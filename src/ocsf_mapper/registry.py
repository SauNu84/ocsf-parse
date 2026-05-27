"""Inventory of mapping configs in a folder.

Used by the linter, the CLI ``list`` subcommand, and (later) the web UI's
homepage. Mapping discovery is by filename convention: ``<name>.json`` in
``mappings/``, with a paired sample at ``samples/<name>.<ext>``.

Files beginning with ``_`` are treated as scratch / smoke outputs and skipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_SAMPLE_EXTS = (".jsonl", ".log", ".json", ".ndjson")


def list_mappings(folder: Path | str = "mappings") -> list[dict]:
    """Return one summary dict per mapping in ``folder``.

    Each summary: ``{name, path, source_name, parser_kind, classes, sample}``.
    ``classes`` is a list of OCSF class names declared by the mapping.
    ``sample`` is the resolved sample path (or ``None`` if none found).
    """
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(folder.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            cfg = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        out.append(
            {
                "name": p.stem,
                "path": str(p),
                "source_name": cfg.get("source_name", p.stem),
                "parser_kind": "json" if cfg.get("parser") == "json" else "regex",
                "classes": sorted(cfg.get("classes", {}).keys()),
                "sample": _find_sample(p.stem, folder.parent / "samples"),
            }
        )
    return out


def _find_sample(name: str, samples_dir: Path) -> Optional[str]:
    """Locate a sample file for a mapping named ``name`` under ``samples_dir``.

    Convention: ``<name>.<ext>``. Falls back to ``<name>_sample.<ext>`` and
    ``<name>_access.<ext>`` so prototype-style names continue to work.
    """
    if not samples_dir.is_dir():
        return None
    for stem in (name, f"{name}_sample", f"{name}_access"):
        for ext in _SAMPLE_EXTS:
            p = samples_dir / f"{stem}{ext}"
            if p.exists():
                return str(p)
    return None
