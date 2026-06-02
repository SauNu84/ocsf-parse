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

    Each summary:
        ``{name, path, source_name, display_name, vendor, priority,
           description, parser_kind, classes, sample}``

    Metadata fields (``display_name``, ``vendor``, ``priority``, ``description``)
    fall back to sensible defaults if the mapping doesn't carry them yet.
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
        parser_spec = cfg.get("parser")
        if parser_spec in ("cef", "leef"):
            parser_kind = parser_spec
        elif parser_spec == "json":
            parser_kind = "json"
        else:
            parser_kind = "regex"
        out.append(
            {
                "name": p.stem,
                "path": str(p),
                "source_name":  cfg.get("source_name", p.stem),
                "display_name": cfg.get("display_name", cfg.get("source_name", p.stem)),
                "vendor":       cfg.get("vendor", "Unknown"),
                "priority":     cfg.get("priority", "medium"),
                "description":  cfg.get("description", ""),
                "mapping_version": cfg.get("mapping_version", "0.0.0"),
                "parser_kind": parser_kind,
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
