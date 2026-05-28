"""PII redaction for OCSF events before they hit a sink.

Addresses PLAN.md §5: "Sample logs contain PII — optional redaction layer."
This module provides:

  * :func:`redact_text` — scrub a single string in-place.
  * :func:`redact_event` — walk an OCSF event dict recursively, scrub
    every string leaf.
  * :class:`RedactingSink` — wrap any :class:`~ocsf_mapper.sinks.Sink` so
    events get redacted on their way through.

Patterns are intentionally conservative — false negatives are preferred
over false positives. The full list:

  ============  ====================================================
  Kind          What it matches
  ============  ====================================================
  ``email``     RFC-5322-ish: ``<local>@<host>.<tld>``
  ``ipv4``      4-octet dotted quad with each octet 0–255
  ``ssn``       9-digit US SSN with optional dashes (NNN-NN-NNNN)
  ``ccn``       12–19 digit number that passes the Luhn checksum
  ``phone``     North-American 10-digit phone numbers (loose)
  ``jwt``       Three base64-url segments separated by dots
  ============  ====================================================

Each match is replaced by ``[REDACTED:<kind>]``. Pass ``kinds=`` to opt
into a subset; the default is the full set.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b"),
    "ipv4":  re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    ),
    "ssn":   re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"),
    "phone": re.compile(r"\b(?:\+?1[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b"),
    "jwt":   re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"),
    # 'ccn' is matched separately because we also Luhn-validate.
    "_ccn_raw": re.compile(r"\b(?:\d[ -]?){12,19}\b"),
}

ALL_KINDS = ("email", "ipv4", "ssn", "phone", "jwt", "ccn")


def _luhn_ok(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if len(digits) < 12 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _redact_ccn(s: str) -> str:
    def repl(m):
        return "[REDACTED:ccn]" if _luhn_ok(m.group(0)) else m.group(0)
    return _PATTERNS["_ccn_raw"].sub(repl, s)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def redact_text(text: str, kinds: Optional[Iterable[str]] = None) -> str:
    """Scrub a single string. Order: email, ipv4, ssn, phone, jwt, ccn.

    The order matters — e.g. emails come before IPs so the local-part of
    ``alice@10.0.0.1`` doesn't get reduced by the IP rule first.
    """
    if not isinstance(text, str):
        return text
    kinds = tuple(kinds) if kinds is not None else ALL_KINDS
    for kind in kinds:
        if kind == "ccn":
            text = _redact_ccn(text)
        elif kind in _PATTERNS:
            text = _PATTERNS[kind].sub(f"[REDACTED:{kind}]", text)
    return text


def redact_event(event, kinds: Optional[Iterable[str]] = None):
    """Recursively scrub string leaves in an OCSF event (dict / list / scalar).

    Non-string leaves are passed through untouched. Keys are never modified.
    """
    if isinstance(event, dict):
        return {k: redact_event(v, kinds) for k, v in event.items()}
    if isinstance(event, list):
        return [redact_event(v, kinds) for v in event]
    if isinstance(event, str):
        return redact_text(event, kinds)
    return event


# ---------------------------------------------------------------------------
# Sink wrapper
# ---------------------------------------------------------------------------


class RedactingSink:
    """Wraps any sink with PII redaction. Delegates ``write_*`` after scrubbing.

    Usage::

        with RedactingSink(JsonlSink("out.jsonl"), kinds=["email", "ipv4"]) as s:
            s.write_many(apply_stream(config, lines))
    """

    def __init__(self, wrapped, kinds: Optional[Iterable[str]] = None) -> None:
        self._wrapped = wrapped
        self._kinds = tuple(kinds) if kinds is not None else ALL_KINDS

    def write_one(self, event: dict) -> None:
        self._wrapped.write_one(redact_event(event, self._kinds))

    def write_many(self, events) -> int:
        n = 0
        for ev in events:
            self.write_one(ev)
            n += 1
        return n

    def close(self) -> None:
        self._wrapped.close()

    def __enter__(self):
        # If the wrapped sink is also a context manager, forward.
        if hasattr(self._wrapped, "__enter__"):
            self._wrapped.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if hasattr(self._wrapped, "__exit__"):
            self._wrapped.__exit__(exc_type, exc, tb)
        else:
            self.close()
