"""NDJSON audit log of mapping-config edits.

Compliance question: "who changed cloudtrail.json last Tuesday?" Without
auth on the web UI, "who" is best-effort — we pick the first non-empty
of ``OCSF_AUDIT_USER`` / ``USER`` / ``USERNAME`` env vars, falling back
to ``"local"``. The audit log records:

  - timestamp (ISO 8601 UTC)
  - user
  - action ("create" | "update")
  - mapping name
  - bytes before / after (for size-delta visibility)
  - lint status ("OK" | "FAIL" | "SKIP" | "REJECTED")
  - error list (empty on success, non-empty on rejected saves)

Each event is one JSON line in ``<root>/audit/mapping_edits.ndjson``.
Append-only by design — never edit in place. Operationally:

    tail -f audit/mapping_edits.ndjson | jq .
    grep '"mapping":"cloudtrail"' audit/mapping_edits.ndjson | jq -s 'sort_by(.ts)'

The audit directory is created lazily on first write.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


_AUDIT_SUBDIR = "audit"
_AUDIT_FILENAME = "mapping_edits.ndjson"


def _resolve_user() -> str:
    for var in ("OCSF_AUDIT_USER", "USER", "USERNAME"):
        v = os.environ.get(var)
        if v:
            return v
    return "local"


def audit_path(root: Path | str) -> Path:
    return Path(root) / _AUDIT_SUBDIR / _AUDIT_FILENAME


def log_edit(
    root: Path | str,
    *,
    mapping: str,
    action: str,
    lint_status: str,
    errors: Optional[Iterable[str]] = None,
    bytes_before: Optional[int] = None,
    bytes_after: Optional[int] = None,
    user: Optional[str] = None,
) -> None:
    """Append one event to the audit log.

    ``mapping`` is the source short name (``cloudtrail``, ``okta``, ...).
    ``action`` is ``"create"`` for new sources via the wizard, ``"update"``
    for the Mapping-tab editor. ``lint_status`` is the result of
    ``lint_one()`` on the candidate file before the save was committed.
    ``errors`` is the lint error list, empty on success.

    Silent best-effort: if the audit directory can't be created we log
    a warning to stderr but don't raise — losing the audit trail
    shouldn't break a save.
    """
    path = audit_path(root)
    record = {
        "ts":           datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user":         user or _resolve_user(),
        "action":       action,
        "mapping":      mapping,
        "lint_status":  lint_status,
        "errors":       list(errors) if errors else [],
        "bytes_before": bytes_before,
        "bytes_after":  bytes_after,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False))
            fp.write("\n")
    except OSError as e:  # pragma: no cover - filesystem fault
        import sys
        print(f"warning: audit log write failed: {e}", file=sys.stderr)


def read_audit(root: Path | str, limit: Optional[int] = None) -> list[dict]:
    """Return the audit log as a list of dicts, newest first.

    ``limit`` truncates to the most-recent N events. Returns an empty
    list if the audit file doesn't exist yet.
    """
    path = audit_path(root)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.reverse()  # newest first
    if limit is not None:
        out = out[:limit]
    return out
