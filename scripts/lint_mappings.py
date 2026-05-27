#!/usr/bin/env python3
"""Thin wrapper around :mod:`ocsf_mapper.lint` for CI invocation.

Usage:
    python scripts/lint_mappings.py [mappings_folder]
"""
import sys

from ocsf_mapper.lint import main

if __name__ == "__main__":
    sys.exit(main())
