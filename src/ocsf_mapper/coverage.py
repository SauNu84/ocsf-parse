"""Per-class completeness score for a mapping config.

For each OCSF class declared by a mapping, this computes how many of the
class's ``required`` and ``recommended`` attributes the mapping actually
populates. We measure declaration coverage (target paths in the mapping
config), not runtime coverage of any specific event — those usually
correlate but the declaration view is what the editor needs.

A mapping target like ``"metadata.product.name"`` is considered to cover
the top-level OCSF attribute ``metadata`` because the prune layer will
keep the parent object as long as any of its children are populated.

Returned shape::

    {
      "<class_name>": {
        "required":            <int>,  # populated count
        "required_total":      <int>,
        "recommended":         <int>,
        "recommended_total":   <int>,
        "missing_required":    [<attr>, ...],
        "missing_recommended": [<attr>, ...],
        "score":               <float in [0,1]>,
      },
      ...
    }
"""

from __future__ import annotations

from typing import Optional

from ocsf_mapper.schema import Schema


def coverage(config: dict, schema: Optional[Schema] = None) -> dict:
    """Return per-class coverage stats for the mapping ``config``."""
    schema = schema or Schema()
    out: dict = {}
    for cls_name, cls_block in (config.get("classes") or {}).items():
        try:
            cls = schema.load_class(cls_name)
        except FileNotFoundError:
            continue
        required = _attrs_by_requirement(cls, "required")
        recommended = _attrs_by_requirement(cls, "recommended")
        prefixes = {t.split(".", 1)[0] for t in (cls_block.get("mapping") or {})}
        req_hit  = [r for r in required if r in prefixes]
        rec_hit  = [r for r in recommended if r in prefixes]
        out[cls_name] = {
            "required":            len(req_hit),
            "required_total":      len(required),
            "recommended":         len(rec_hit),
            "recommended_total":   len(recommended),
            "missing_required":    [r for r in required if r not in prefixes],
            "missing_recommended": [r for r in recommended if r not in prefixes],
            "score": _score(len(req_hit), len(required), len(rec_hit), len(recommended)),
        }
    return out


def summary(coverage_dict: dict) -> dict:
    """Roll a per-class coverage dict up into one set of totals."""
    req_h = req_t = rec_h = rec_t = 0
    for v in coverage_dict.values():
        req_h += v["required"]
        req_t += v["required_total"]
        rec_h += v["recommended"]
        rec_t += v["recommended_total"]
    return {
        "required":            req_h,
        "required_total":      req_t,
        "recommended":         rec_h,
        "recommended_total":   rec_t,
        "score": _score(req_h, req_t, rec_h, rec_t),
    }


def _attrs_by_requirement(cls: dict, level: str) -> list[str]:
    return [
        n for n, sp in cls.get("attributes", {}).items()
        if isinstance(sp, dict)
        and not n.startswith("$")
        and sp.get("requirement") == level
    ]


def _score(req_h: int, req_t: int, rec_h: int, rec_t: int) -> float:
    """Weighted score: required attrs count 2x recommended."""
    weighted_hit = (req_h * 2) + rec_h
    weighted_tot = (req_t * 2) + rec_t
    return weighted_hit / weighted_tot if weighted_tot else 1.0
