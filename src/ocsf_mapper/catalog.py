"""Master-data view of all log sources.

Reads ``catalog.json`` at the repo root, joins it with the on-disk ``mappings/``
folder, and prints a screenshot-style table:

    LOG SOURCE              VENDOR              OCSF CATEGORY            OCSF CLASS              PRIORITY
    Windows Event Log       Microsoft           Identity & Access Mgmt   Authentication           critical
    Sysmon                  Microsoft           System Activity          Process Activity         critical
    ...

Run via:

    python -m ocsf_mapper.catalog [catalog.json]

The catalog is the source of truth for vendor, priority, and use-case
description. The mapping files carry these same fields (injected from the
catalog) so individual ``mappings/*.json`` are self-describing too — but the
catalog adds a defined order, the schema version pin, and the priority legend.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from ocsf_mapper.registry import list_mappings


PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def load_catalog(path: Path | str = "catalog.json") -> dict:
    """Load the catalog file. Raises FileNotFoundError if missing."""
    return json.loads(Path(path).read_text())


def join_catalog(
    catalog_path: Path | str = "catalog.json",
    mappings_dir: Path | str = "mappings",
) -> list[dict]:
    """Return one row per catalog entry, joined with whatever's on disk.

    Each row includes catalog metadata + a ``status`` field:
      - ``"mapped"`` if the source has a mapping file
      - ``"missing"`` if the catalog lists it but the file isn't there
    """
    catalog = load_catalog(catalog_path)
    have = {m["name"]: m for m in list_mappings(mappings_dir)}
    rows = []
    for entry in catalog["entries"]:
        src = entry["source"]
        mapped = src in have
        rows.append(
            {
                **entry,
                "status": "mapped" if mapped else "missing",
                "sample_path": have[src]["sample"] if mapped else None,
                "mapping_path": have[src]["path"] if mapped else None,
            }
        )
    return rows


def print_table(rows: list[dict]) -> None:
    """Print the catalog as a fixed-width table sorted by priority then vendor."""
    rows = sorted(rows, key=lambda r: (PRIORITY_ORDER.get(r["priority"], 99), r["vendor"], r["source"]))

    cols = [
        ("LOG SOURCE",      lambda r: r["display_name"],         34),
        ("VENDOR",          lambda r: r["vendor"],               22),
        ("OCSF CATEGORY",   lambda r: r["ocsf"]["category_name"], 32),
        ("OCSF CLASS",      lambda r: r["ocsf"]["class_name"],    24),
        ("CLS_UID",         lambda r: str(r["ocsf"]["class_uid"]), 8),
        ("PRIORITY",        lambda r: r["priority"],              9),
        ("STATUS",          lambda r: r["status"],                8),
    ]

    header = "  ".join(f"{name:<{w}}" for name, _, w in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        line = "  ".join(f"{str(fn(r) or '')[:w]:<{w}}" for _, fn, w in cols)
        print(line)
    print()
    print(f"Total: {len(rows)} sources")
    by_pri: dict = {}
    for r in rows:
        by_pri[r["priority"]] = by_pri.get(r["priority"], 0) + 1
    for pri in ("critical", "high", "medium", "low"):
        if pri in by_pri:
            print(f"  {pri}: {by_pri[pri]}")


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    catalog_path = argv[0] if argv else "catalog.json"
    try:
        rows = join_catalog(catalog_path)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
