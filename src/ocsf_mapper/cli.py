"""Command-line entry point: ``ocsf-mapper <subcommand>``.

Subcommands::

    apply     <mapping> <input> [output]   map a log file to OCSF events
    validate  <events>  <class>            structural-validate already-OCSF events
    list      [--folder mappings]          list known mappings
    catalog   [<catalog.json>]             print the master-data table
    lint      [<folder>]                   CI gate (alias for `python -m ocsf_mapper.lint`)

Use ``-`` as ``<input>`` to read from stdin; use ``-`` (or omit) as the output
to write JSONL to stdout. The sink kind is inferred from the output's
extension and can be overridden with ``--sink``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

from ocsf_mapper.apply import apply_stream
from ocsf_mapper.registry import list_mappings
from ocsf_mapper.schema import Schema
from ocsf_mapper.sinks import get_sink, infer_kind
from ocsf_mapper.validate import validate


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read_lines(input_arg: str) -> Iterable[str]:
    """Read newline-delimited lines from a path or stdin (``-``)."""
    if input_arg == "-":
        return iter(sys.stdin)
    return Path(input_arg).read_text().splitlines()


def _load_mapping(mapping_arg: str) -> dict:
    return json.loads(Path(mapping_arg).read_text())


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_apply(args: argparse.Namespace) -> int:
    config = _load_mapping(args.mapping)
    lines = _read_lines(args.input)

    sink_kind = args.sink or infer_kind(args.output)
    sink_path = None if sink_kind == "stdout" else args.output
    if sink_kind != "stdout" and sink_path is None:
        # No output given but a non-stdout sink requested → require a path.
        print(f"error: --sink {sink_kind} requires an output path", file=sys.stderr)
        return 2

    sink = get_sink(sink_kind, sink_path)
    if args.redact:
        from ocsf_mapper.redact import ALL_KINDS, RedactingSink
        kinds = ALL_KINDS if args.redact == ["all"] else args.redact
        sink = RedactingSink(sink, kinds=kinds)

    with sink:
        n = sink.write_many(apply_stream(config, lines))
    # Status goes to stderr so stdout stays clean for piping.
    if sink_kind == "stdout":
        print(f"wrote {n} event(s) -> stdout", file=sys.stderr)
    else:
        print(f"wrote {n} event(s) -> {sink_path}", file=sys.stderr)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    schema = Schema()
    events = [json.loads(l) for l in _read_lines(args.events) if l.strip()]
    failures = 0
    for i, ev in enumerate(events, 1):
        errs = validate(ev, args.class_name, schema=schema)
        if errs:
            failures += 1
            print(f"event #{i}: FAIL")
            for e in errs:
                print(f"  - {e}")
    print(f"\nPASSED: {len(events) - failures}    FAILED: {failures}")
    return 0 if failures == 0 else 1


def cmd_list(args: argparse.Namespace) -> int:
    rows = list_mappings(args.folder)
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print(f"no mappings in {args.folder}/")
        return 0
    cols = [
        ("NAME",         lambda r: r["name"],         22),
        ("DISPLAY",      lambda r: r["display_name"], 32),
        ("VENDOR",       lambda r: r["vendor"],       18),
        ("PRIORITY",     lambda r: r["priority"],     9),
        ("PARSER",       lambda r: r["parser_kind"],  7),
        ("CLASSES",      lambda r: ",".join(r["classes"]), 28),
    ]
    header = "  ".join(f"{n:<{w}}" for n, _, w in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        print("  ".join(f"{str(fn(r) or '')[:w]:<{w}}" for _, fn, w in cols))
    print(f"\n{len(rows)} mapping(s)")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    from ocsf_mapper.catalog import main as catalog_main
    return catalog_main([args.catalog] if args.catalog else [])


def cmd_lint(args: argparse.Namespace) -> int:
    from ocsf_mapper.lint import main as lint_main
    return lint_main(args.folder)


def cmd_schema_diff(args: argparse.Namespace) -> int:
    from ocsf_mapper.schema_diff import main as schema_diff_main
    return schema_diff_main([args.ref, args.folder])


def cmd_generate(args: argparse.Namespace) -> int:
    from ocsf_mapper.generate import generate
    from ocsf_mapper.providers import get_provider

    provider = get_provider(name=args.provider) if args.provider else get_provider()
    config = generate(args.sample, args.source, provider=provider)
    output = json.dumps(config, indent=2)
    if args.output and args.output != "-":
        Path(args.output).write_text(output + "\n")
        print(f"wrote mapping for {args.source!r} -> {args.output}", file=sys.stderr)
    else:
        print(output)
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    """``tail -f``-style live mapping. Ctrl+C to stop."""
    config = _load_mapping(args.mapping)
    sink_kind = args.sink or infer_kind(args.output)
    sink_path = None if sink_kind == "stdout" else args.output
    if sink_kind != "stdout" and sink_path is None:
        print(f"error: --sink {sink_kind} requires an output path", file=sys.stderr)
        return 2
    from ocsf_mapper.stream import stream_apply

    print(
        f"tailing {args.input} → {sink_kind} (Ctrl+C to stop)",
        file=sys.stderr,
    )
    import threading
    stop = threading.Event()
    try:
        with get_sink(sink_kind, sink_path) as sink:
            stream_apply(
                config, args.input, sink,
                poll_interval=args.poll_interval,
                from_start=args.from_start,
                stop=stop,
            )
    except KeyboardInterrupt:
        stop.set()
        print(file=sys.stderr)  # newline after ^C
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "error: 'serve' requires the web deps. "
            "Install with: pip install ocsf-mapper[web]",
            file=sys.stderr,
        )
        return 2
    from ocsf_mapper.web import create_app

    app = create_app(root=args.root or Path.cwd())
    print(f"ocsf-mapper UI ready at http://{args.host}:{args.port}", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ocsf-mapper", description="Map log sources to OCSF events.")
    sub = p.add_subparsers(dest="cmd", required=True)

    # apply
    sp = sub.add_parser("apply", help="Map a log file to OCSF events.")
    sp.add_argument("mapping", help="Path to a mappings/<name>.json file.")
    sp.add_argument("input",   help="Path to the log file, or '-' for stdin.")
    sp.add_argument("output",  nargs="?", default=None,
                    help="Output path. Omit or use '-' for stdout (JSONL).")
    sp.add_argument(
        "--sink",
        choices=["jsonl", "csv", "parquet", "security-lake", "stdout"],
        help="Force sink kind (default: infer from output extension). "
             "'security-lake' writes partitioned Parquet to "
             "<output>/<class_uid>/eventDay=YYYYMMDD/*.parquet.",
    )
    sp.add_argument(
        "--redact",
        nargs="*",
        metavar="KIND",
        default=None,
        help="Redact PII before writing. Pass kinds to opt into a subset "
             "(email ipv4 ssn phone jwt ccn), or just --redact for all.",
    )
    sp.set_defaults(func=cmd_apply)

    # validate
    sp = sub.add_parser("validate", help="Validate OCSF events against a class.")
    sp.add_argument("events", help="JSONL file of OCSF events, or '-' for stdin.")
    sp.add_argument("class_name", help="OCSF class name (e.g. authentication).")
    sp.set_defaults(func=cmd_validate)

    # list
    sp = sub.add_parser("list", help="List available mappings.")
    sp.add_argument("--folder", default="mappings", help="Mappings folder (default: ./mappings).")
    sp.add_argument("--format", choices=["table", "json"], default="table")
    sp.set_defaults(func=cmd_list)

    # catalog
    sp = sub.add_parser("catalog", help="Print the master-data catalog table.")
    sp.add_argument("catalog", nargs="?", default=None, help="Path to catalog.json (default: ./catalog.json).")
    sp.set_defaults(func=cmd_catalog)

    # lint
    sp = sub.add_parser("lint", help="Run all mappings against their pinned samples.")
    sp.add_argument("folder", nargs="?", default="mappings", help="Mappings folder.")
    sp.set_defaults(func=cmd_lint)

    # schema-diff
    sp = sub.add_parser(
        "schema-diff",
        help="Compare current OCSF schema vs an older git ref of the submodule.",
    )
    sp.add_argument("ref", nargs="?", default="HEAD~1",
                    help="Git ref in the ocsf-schema submodule (default: HEAD~1).")
    sp.add_argument("folder", nargs="?", default="mappings",
                    help="Mappings folder (default: ./mappings).")
    sp.set_defaults(func=cmd_schema_diff)

    # generate
    sp = sub.add_parser("generate", help="LLM-draft a new mapping from a sample.")
    sp.add_argument("source", help="Short source name (e.g. 'suricata_alert').")
    sp.add_argument("sample", help="Path to a sample log file.")
    sp.add_argument("output", nargs="?", default=None,
                    help="Output path for the draft mapping (default: stdout).")
    sp.add_argument("--provider", choices=["anthropic", "openai", "fixture"],
                    help="Force LLM provider (default: env-detect).")
    sp.set_defaults(func=cmd_generate)

    # tail
    sp = sub.add_parser("tail",
                        help="tail -f a log file and emit OCSF events live.")
    sp.add_argument("mapping", help="Path to a mappings/<name>.json file.")
    sp.add_argument("input",   help="Path to the log file to tail.")
    sp.add_argument("output",  nargs="?", default=None,
                    help="Output path. Omit or use '-' for stdout (JSONL).")
    sp.add_argument("--sink",
                    choices=["jsonl", "csv", "parquet", "security-lake", "stdout"],
                    help="Force sink kind (default: infer from output).")
    sp.add_argument("--poll-interval", type=float, default=0.5,
                    help="Polling interval in seconds when there are no new lines.")
    sp.add_argument("--from-start", action="store_true",
                    help="Begin from the start of the file (default: from EOF).")
    sp.set_defaults(func=cmd_tail)

    # serve
    sp = sub.add_parser("serve", help="Run the local web UI (Phase B).")
    sp.add_argument("--host", default="127.0.0.1",
                    help="Bind host (default: 127.0.0.1 — localhost only).")
    sp.add_argument("--port", type=int, default=8000,
                    help="Bind port (default: 8000).")
    sp.add_argument("--root", default=None,
                    help="Repo root containing mappings/ and samples/ (default: cwd).")
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
