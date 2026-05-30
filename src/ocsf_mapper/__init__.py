"""ocsf_mapper — declarative log-to-OCSF mapper.

Public API (stable surface, populated as modules land in Phase A):

    from ocsf_mapper import apply, apply_stream

The CLI lives at ``ocsf_mapper.cli`` (coming in Phase A).
"""

from ocsf_mapper.apply import (
    apply,
    apply_stream,
    apply_with_class,
    apply_stream_with_class,
)
from ocsf_mapper.validate import validate, validate_stream
from ocsf_mapper.schema import Schema
from ocsf_mapper.registry import list_mappings

# `ocsf_mapper.catalog` is a CLI module — import it directly when needed
# (avoids `python -m ocsf_mapper.catalog` double-import warning).

__all__ = [
    "apply",
    "apply_stream",
    "apply_with_class",
    "apply_stream_with_class",
    "validate",
    "validate_stream",
    "Schema",
    "list_mappings",
]
__version__ = "0.2.0"
