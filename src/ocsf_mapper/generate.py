"""LLM-assisted mapping generator.

Two-phase design:

  1. ``suggest_classes(sample, source)`` — show the model the class catalog
     (caption + description) and a few sample log lines; it picks 1–N OCSF
     classes plus the routing field that distinguishes them.

  2. ``draft_mapping(sample, source, routing, classes)`` — show the model the
     full attribute schema for the chosen class(es) plus a relevant slice of
     the dictionary; it emits a JSON mapping config conforming to the DSL.

Both phases call through the :class:`~ocsf_mapper.providers.LLMProvider`
abstraction, so the same code works with Anthropic, OpenAI, or the offline
fixture provider used in tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ocsf_mapper.providers import LLMProvider, get_provider
from ocsf_mapper.schema import Schema


# ---------------------------------------------------------------------------
# prompt builders
# ---------------------------------------------------------------------------


def _class_catalog(schema: Schema) -> str:
    rows = []
    for c in schema.class_summaries():
        rows.append(
            f"  - {c['name']}  ({c['category']})  — {c['caption']}: "
            f"{(c['description'] or '')[:140]}"
        )
    return "\n".join(rows)


def prompt_class_selection(sample_lines: list[str], source: str, schema: Schema) -> str:
    sample = "\n".join(sample_lines[:3])
    catalog = _class_catalog(schema)
    return f"""You are choosing the right OCSF event class(es) for a new log source.

SOURCE NAME: {source}

SAMPLE LOG LINES (up to 3):
{sample}

Available OCSF event classes (name, category, caption, short description):
{catalog}

TASK: Return strict JSON with this shape:
{{
  "routing_field": "<JSON path or regex group used to decide which class>",
  "classes": [
    {{ "ocsf_class_name": "<one of the names above>", "matches": ["<source value>", ...], "is_default": false }}
  ]
}}
Rules:
- Choose ONE class if all events go to the same class (mark is_default=true with no matches).
- Otherwise choose 2-4 classes and provide the source-side values that route to each.
- Prefer specific classes (http_activity, authentication, api_activity) over generic ones.
"""


def prompt_mapping(sample_lines: list[str], source: str, routing: dict, classes: list[str], schema: Schema) -> str:
    parts = ["OCSF SCHEMA CONTEXT (truncated for brevity)\n"]
    used_attrs: set = set()
    for cls in classes:
        full = schema.load_class(cls)
        parts.append(f"\n=== class: {cls} ===")
        parts.append(json.dumps({
            "name": full["name"],
            "caption": full.get("caption"),
            "category": full.get("category"),
            "extends": full.get("extends"),
            "constraints": full.get("constraints", {}),
            "attributes": {
                k: {
                    "requirement": v.get("requirement"),
                    "group": v.get("group"),
                    "enum": v.get("enum"),
                    "description": (v.get("description") or "")[:200],
                }
                for k, v in full.get("attributes", {}).items() if not k.startswith("$")
            },
        }, indent=2))
        used_attrs.update(full.get("attributes", {}).keys())

    dictionary = schema.dictionary().get("attributes", {})
    dict_subset = {k: dictionary[k] for k in used_attrs if k in dictionary}
    parts.append(f"\n=== relevant attribute dictionary (subset, {len(dict_subset)} entries) ===")
    parts.append(json.dumps({k: {"caption": v.get("caption"),
                                  "type": v.get("type"),
                                  "description": (v.get("description") or "")[:160]}
                              for k, v in dict_subset.items()}, indent=2))

    sample = "\n".join(sample_lines[:3])
    return f"""{"".join(parts)}

SOURCE NAME: {source}
ROUTING DECISION (from phase 1): {json.dumps(routing, indent=2)}
SAMPLE LOG LINES:
{sample}

TASK: Produce a JSON mapping config conforming to this DSL (executed by apply.py):

{{
  "source_name": "<name>",
  "parser": "json"  |  {{ "regex": "<pattern>", "groups": ["..."] }},
  "routing": {{
    "field": "<JSON path used to decide class>",
    "rules": [
      {{ "matches": ["..."], "class": "<class_name>" }},
      {{ "default": true, "class": "<class_name>" }}
    ]
  }},
  "classes": {{
    "<class_name>": {{
      "mapping": {{
        "<dotted.target.path>": <op>,
        ...
      }}
    }}
  }}
}}

Available <op> forms:
  {{"const": <any>}}                          fixed value
  {{"path": "$.dotted.source.path"}}          read from parsed record
  {{"group": "<regex group>"}}                read regex group (only if parser=regex)
  {{"lookup": "<expr>", "table": {{...}}, "default": <any>, "prefix_match": false}}
  {{"time":   "<expr>", "format": "iso8601"|"epoch_ms"|"epoch_s"|"strptime:%Y-..."}}
  {{"range":  "<expr>", "ranges": [[low,high,value],...], "default": <any>}}
  {{"raw": true}}                             original raw line
  {{"expr": "class_uid * 100 + activity_id"}} (sandboxed; access already-set ints/strings)
  {{"int": "<expr>"}}, {{"bool": "<expr>"}}   type coercion
  {{"for_each": "<expr>", "as": "x", "map": {{...}}}}   array fan-out

REQUIREMENTS:
- Populate ALL required attributes (per `requirement: required` and class constraints).
- Set metadata.version="1.9.0-dev", metadata.product.name=<source>, metadata.product.vendor_name=<vendor>.
- Use correct class_uid / category_uid integers (system=1, findings=2, iam=3, network=4, discovery=5, application=6).
- Compute type_uid via expr.
- Map vendor enums to OCSF integer ids using lookup tables.
- Preserve the original record via "raw_data": {{"raw": true}}.

OUTPUT: emit ONLY the JSON config, no commentary. JSON.
"""


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------


def suggest_classes(
    sample_lines: list[str],
    source: str,
    *,
    provider: Optional[LLMProvider] = None,
    schema: Optional[Schema] = None,
) -> dict:
    """Phase 1: ask the LLM which OCSF class(es) fit this source.

    Returns a dict ``{"routing_field": ..., "classes": [{"ocsf_class_name": ...}]}``.
    Raises ``ValueError`` if the response isn't valid JSON or contains unknown
    class names (post-LLM safeguard per PLAN.md §3 Phase C).
    """
    schema = schema or Schema()
    provider = provider or get_provider()
    prompt = prompt_class_selection(sample_lines, source, schema)
    raw = provider.complete(prompt)
    routing = json.loads(_strip_codefence(raw))
    known = {c["name"] for c in schema.class_summaries()}
    for c in routing.get("classes", []):
        if c["ocsf_class_name"] not in known:
            raise ValueError(
                f"LLM returned unknown OCSF class: {c['ocsf_class_name']!r}"
            )
    return routing


def draft_mapping(
    sample_lines: list[str],
    source: str,
    routing: dict,
    *,
    provider: Optional[LLMProvider] = None,
    schema: Optional[Schema] = None,
) -> dict:
    """Phase 2: ask the LLM to produce a full mapping config."""
    schema = schema or Schema()
    provider = provider or get_provider()
    classes = [c["ocsf_class_name"] for c in routing["classes"]]
    prompt = prompt_mapping(sample_lines, source, routing, classes, schema)
    raw = provider.complete(prompt)
    return json.loads(_strip_codefence(raw))


def generate(
    sample_path: Path | str,
    source: str,
    *,
    provider: Optional[LLMProvider] = None,
    schema: Optional[Schema] = None,
) -> dict:
    """One-call wrapper around phases 1 and 2. Returns a full mapping config."""
    lines = [l for l in Path(sample_path).read_text().splitlines() if l.strip()][:10]
    schema = schema or Schema()
    provider = provider or get_provider()
    routing = suggest_classes(lines, source, provider=provider, schema=schema)
    return draft_mapping(lines, source, routing, provider=provider, schema=schema)


# ---------------------------------------------------------------------------
# fix mode — repair an existing mapping that fails lint
# ---------------------------------------------------------------------------


def prompt_fix(
    current: dict,
    errors: list[str],
    sample_lines: list[str],
    schema: Schema,
) -> str:
    """Build the LLM prompt for the Mapping-tab 'Fix with AI' flow.

    Smaller than the from-scratch generate prompt — the LLM already has
    a working skeleton; it only needs to fix the specific errors. We
    include the schema context for the classes the mapping targets
    (filtered to required + recommended attributes, the most common
    fix surface) plus a few sample log lines for path verification.
    """
    classes = list((current.get("classes") or {}).keys())
    parts: list[str] = [
        "You are fixing a failing OCSF mapping config.\n",
        "\n=== CURRENT MAPPING (broken) ===\n",
        json.dumps(current, indent=2),
        "\n\n=== LINTER ERRORS (against the pinned sample) ===\n",
    ]
    for e in errors[:30]:
        parts.append(f"  - {e}\n")
    if len(errors) > 30:
        parts.append(f"  …and {len(errors) - 30} more error(s) elided\n")

    if sample_lines:
        parts.append("\n=== SAMPLE LOG LINES (first 5) ===\n")
        for s in sample_lines[:5]:
            parts.append(s + "\n")

    if classes:
        parts.append("\n=== OCSF SCHEMA CONTEXT (required + recommended attrs only) ===\n")
        for cls in classes:
            try:
                full = schema.load_class(cls)
            except Exception:
                continue
            parts.append(f"\n--- class: {cls} ---\n")
            parts.append(json.dumps({
                "constraints": full.get("constraints", {}),
                "attributes": {
                    k: {
                        "requirement": v.get("requirement"),
                        "group": v.get("group"),
                        "enum": v.get("enum"),
                        "type": v.get("type"),
                        "description": (v.get("description") or "")[:160],
                    }
                    for k, v in full.get("attributes", {}).items()
                    if not k.startswith("$")
                    and v.get("requirement") in ("required", "recommended")
                },
            }, indent=2))
            parts.append("\n")

    parts.append("""
TASK: Return the FIXED mapping JSON. Preserve all valid fields and ops
from the current mapping; ONLY add/change what's needed to satisfy the
linter errors above. Do not rewrite working sections.

Available <op> forms (executed by apply.py):
  {"const": <any>}, {"path": "$.dotted.path"}, {"group": "<regex group>"},
  {"lookup": "<expr>", "table": {...}, "default": <any>, "prefix_match": false},
  {"time": "<expr>", "format": "iso8601"|"epoch_ms"|"epoch_s"|"strptime:%Y-..."},
  {"range": "<expr>", "ranges": [[low,high,value],...], "default": <any>},
  {"expr": "class_uid * 100 + activity_id"}, {"int": "..."}, {"bool": "..."},
  {"raw": true}, {"for_each": "<expr>", "as": "x", "map": {...}}

OUTPUT: emit ONLY the JSON config, no commentary, no code fences. JSON.
""")
    return "".join(parts)


def fix_mapping(
    current: dict,
    errors: list[str],
    sample_lines: list[str],
    *,
    provider: Optional[LLMProvider] = None,
    schema: Optional[Schema] = None,
) -> dict:
    """Ask the LLM to repair a broken mapping.

    ``current`` is the user's in-progress mapping (must be valid JSON).
    ``errors`` is the list of lint errors from a recent save attempt.
    ``sample_lines`` is the first few raw lines of the pinned sample —
    helps the LLM verify field paths exist in the input.

    Returns the repaired mapping as a dict. The web layer is expected
    to re-lint the result before promoting it; this function makes no
    guarantee about correctness, only that the response is valid JSON.
    """
    schema = schema or Schema()
    provider = provider or get_provider()
    prompt = prompt_fix(current, errors, sample_lines, schema)
    raw = provider.complete(prompt)
    return json.loads(_strip_codefence(raw))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _strip_codefence(text: str) -> str:
    """LLMs sometimes wrap JSON in ```json ... ``` — strip it if present."""
    text = text.strip()
    if text.startswith("```"):
        # remove opening fence (with optional language tag)
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()
