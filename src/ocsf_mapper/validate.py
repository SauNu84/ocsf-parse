"""Lightweight structural validator for mapped OCSF events.

What this checks (per event, for a given OCSF class):

  * Required attributes are present (per ``requirement: required`` in the class,
    merged across the ``extends`` chain).
  * Class-level ``at_least_one`` constraints are satisfied.
  * ``activity_id`` is in the class's declared enum (plus the universal 0/99 OCSF
    sentinels).
  * ``category_uid`` resolves to a known category in ``categories.json``.

What this does NOT check (yet): full attribute-type coercion, profile resolution,
deep object-shape validation. That's what the full ``ocsf-validator`` PyPI
package is for. This validator runs in CI on every mapping change, so it has to
stay fast and dependency-free.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from ocsf_mapper.schema import Schema


def required_attrs(cls: Mapping) -> list[str]:
    """List attribute names with ``requirement: required`` for this class."""
    out = []
    for name, spec in cls.get("attributes", {}).items():
        if name.startswith("$"):
            continue
        if isinstance(spec, dict) and spec.get("requirement") == "required":
            out.append(name)
    return out


def validate(
    event: Mapping,
    class_name: str,
    schema: Optional[Schema] = None,
) -> list[str]:
    """Return a list of human-readable issues for ``event``. Empty list = valid."""
    schema = schema or Schema()
    cls = schema.load_class(class_name)
    errors: list[str] = []

    # required attributes (top-level)
    for r in required_attrs(cls):
        if r not in event:
            errors.append(f"missing required attribute: {r}")

    # class-level constraints
    for kind, names in cls.get("constraints", {}).items():
        if kind == "at_least_one":
            if not any(n in event for n in names):
                errors.append(f"constraint at_least_one violated: need one of {names}")

    # activity_id enum
    enum = cls.get("attributes", {}).get("activity_id", {}).get("enum", {})
    if enum and "activity_id" in event:
        allowed = {int(k) for k in enum.keys()} | {0, 99}
        if event["activity_id"] not in allowed:
            errors.append(
                f"activity_id={event['activity_id']} not in enum {sorted(allowed)}"
            )

    # category_uid sanity
    if "category_uid" in event:
        cats = schema.categories().get("attributes", {})
        known = {c.get("uid") for c in cats.values()}
        if event["category_uid"] not in known:
            errors.append(
                f"category_uid={event['category_uid']} not a known category"
            )

    return errors


def validate_stream(
    events: Iterable[Mapping],
    class_name: str,
    schema: Optional[Schema] = None,
) -> list[tuple[int, list[str]]]:
    """Validate many events. Returns ``[(index, errors), ...]`` for failing events only."""
    schema = schema or Schema()
    out = []
    for i, ev in enumerate(events):
        errs = validate(ev, class_name, schema=schema)
        if errs:
            out.append((i, errs))
    return out
