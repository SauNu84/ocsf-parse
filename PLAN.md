# ocsf-parse — Implementation Plan

> Self-service platform to map any log source into the [Open Cybersecurity Schema
> Framework](https://github.com/ocsf/ocsf-schema). One Python engine, JSON mapping
> configs per source, LLM-assisted onboarding, validating UI.

> **Implementation status:** Phase A, B, C complete; Phase D in progress.
> 29 reference mappings live, 176 tests passing on Python 3.9 / 3.11 / 3.12.
> See [`CHANGELOG.md`](./CHANGELOG.md) for the commit timeline. Status
> tags on each phase below; original design rationale preserved verbatim.

## 1. Vision

Security teams shouldn't have to write a custom parser every time they add a new
log source. This project provides:

1. A small **Python SDK** that loads a declarative mapping config and emits valid
   OCSF events from raw log lines (JSON or text).
2. An **LLM-assisted generator** that drafts a new mapping from a sample log +
   the OCSF schema. One LLM call per source; zero per event.
3. A **Web UI** that lists available mappings, lets users upload a sample and
   see the OCSF output, validates against the schema, and walks them through
   generating a mapping for a new source.
4. A **CI lint** that runs every mapping against its pinned sample on every PR
   and fails the build if any event no longer conforms to OCSF.

Mappings live as JSON files in `mappings/`, samples in `samples/`. Everything is
git-backed, diffable, and reviewable.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ocsf_mapper  (Python SDK, pip-installable)                              │
│  ──────────────────────────────────────────                              │
│    apply(config, record)            → OCSF dict                          │
│    apply_stream(config, lines)      → Iterator[OCSF dict]                │
│    validate(event, class_name)      → list[str] of issues                │
│    generate(sample, source)         → mapping config (LLM-assisted)      │
│    list_mappings(folder)            → registry                           │
│    coverage(config, schema)         → completeness score per class       │
│                                                                          │
│    providers/  (LLM abstraction)    AnthropicProvider, OpenAIProvider    │
│    sinks/      (output abstraction) JsonlSink, ParquetSink, CsvSink      │
└──────────────────────────────────────────────────────────────────────────┘
              ▲                       ▲                        ▲
              │                       │                        │
     ┌────────┴──────┐        ┌───────┴────────┐       ┌───────┴────────┐
     │  Web UI       │        │  CLI           │       │  CI lint       │
     │  FastAPI +    │        │  ocsf-mapper   │       │  scripts/lint  │
     │  Jinja2 +     │        │   apply | tail │       │                │
     │  HTMX         │        │   generate     │       │                │
     └───────────────┘        └────────────────┘       └────────────────┘
```

The SDK is the engine. The UI is one consumer. CI and batch pipelines are
others. **No UI-only code paths**: anything the UI can do must be expressible
through the SDK API.

### Repository layout

```
ocsf-parse/
├── PLAN.md                       this file
├── README.md                     quickstart
├── pyproject.toml                package metadata
├── src/
│   └── ocsf_mapper/
│       ├── __init__.py           public API surface
│       ├── apply.py              DSL interpreter (formerly apply_mapping.py)
│       ├── validate.py           schema validator (formerly validate_ocsf.py)
│       ├── generate.py           LLM-assisted generator (formerly gen_mapping.py)
│       ├── schema.py             schema loader (categories, classes, dictionary)
│       ├── registry.py           list/inspect mappings
│       ├── ops.py                op kinds (const, path, lookup, time, ...)
│       └── coverage.py           per-class field-completeness score
├── tests/
│   ├── test_apply.py
│   ├── test_validate.py
│   ├── test_generate.py
│   └── fixtures/                 minimal mapping + sample pairs per op kind
├── mappings/
│   ├── nginx.json
│   ├── cloudtrail.json
│   ├── okta.json
│   └── palo_alto.json
├── samples/
│   ├── nginx_access.log
│   ├── cloudtrail.jsonl
│   ├── okta_sample.jsonl
│   └── palo_alto.log
├── web/
│   ├── app.py                    FastAPI app
│   ├── templates/                Jinja2 templates (homepage, source detail, …)
│   ├── static/                   minimal CSS, no JS framework
│   └── routes/                   modular route handlers
├── scripts/
│   ├── lint_mappings.py          CI gate (one-line entry point to ocsf_mapper.lint)
│   └── ingest.py                 example batch consumer
├── .github/
│   └── workflows/
│       ├── ci.yml                pytest + lint_mappings on every PR
│       └── publish.yml           tag → PyPI publish
└── ocsf-schema/                  vendored or git-submodule of ocsf/ocsf-schema
```

---

## 3. Phases

Each phase has explicit acceptance criteria. Don't move on until they're green.
See §7 (sinks), §8 (batch/stream), §9 (LLM providers) for cross-cutting details.

### Phase A — SDK (~2–3 days) — ✅ DONE

**Status:** Shipped in commits `afff28a` … `bffc0c8` … `0b2d37e`. Scope
expanded along the way: 29 reference mappings (vs. the 4 planned), 8 CLI
subcommands (vs. 5 planned), and 5 sink kinds (vs. 3 planned).

**Goal:** Wrap the engine code into a clean, pip-installable Python package.

#### Deliverables

- `src/ocsf_mapper/` package with the public API listed above.
- `pyproject.toml` (PEP 621); `pip install -e .` works.
- `tests/` with pytest coverage of every op kind in the DSL.
- 4 reference mappings (nginx, cloudtrail, okta, palo_alto) + paired samples
  copied in from the prototype at `/tmp/ocsf-demo/`.
- `scripts/lint_mappings.py` runs against `mappings/` and exits non-zero on
  failure.
- **LLM provider abstraction** (`providers/`) — `AnthropicProvider` and
  `OpenAIProvider` both implementing a `LLMProvider` protocol; auto-detection
  from env. See §12.
- **Output sink abstraction** (`sinks/`) — `JsonlSink`, `CsvSink`,
  `ParquetSink` (optional, requires `pyarrow`). See §10.
- **`ocsf-schema` as a git submodule** under `ocsf-schema/`. Schema loader
  reads from this path; no network calls at runtime.
- **CLI entry point** `ocsf-mapper`:
  - `ocsf-mapper apply <mapping> <input> [<output>]` (batch — file or `-` for stdin)
  - `ocsf-mapper validate <events.jsonl> <class>`
  - `ocsf-mapper generate <source_name> <sample>`
  - `ocsf-mapper list` (registry)
  - `ocsf-mapper lint` (CI gate)

#### DSL ops (must be supported)

| Op | Purpose |
|---|---|
| `{"const": v}` | literal value |
| `{"path": "$.a.b"}` | JSON path (supports list indices) |
| `{"group": "name"}` | named regex group |
| `{"lookup": expr, "table": {...}, "default": x, "if_null": y, "prefix_match": bool}` | vendor enum → OCSF enum |
| `{"time": expr, "format": "iso8601"\|"epoch_ms"\|"epoch_s"\|"strptime:<fmt>"}` | timestamp → epoch ms (`epoch_s` added during Phase A) |
| `{"range": expr, "ranges": [[lo, hi, val], …], "default": x}` | numeric bucketing (HTTP code → status_id) |
| `{"raw": true}` | original raw record |
| `{"expr": "class_uid * 100 + activity_id"}` | sandboxed arithmetic on already-set targets |
| `{"int": expr}`, `{"bool": expr}` | type coercion |
| **NEW** `{"for_each": expr, "as": "x", "map": {...}}` | array fan-out (e.g. CloudTrail `resources[]`) |

#### Acceptance criteria

- [x] `pytest` passes (≥ 90% coverage of `ocsf_mapper`) — 91%+ across 176 tests
- [x] `python -m ocsf_mapper.lint mappings/` exits 0 with all reference mappings
      — passes on all **29** (vs. 4 originally planned)
- [x] All reference mappings produce events that validate against the schema
- [x] `from ocsf_mapper import apply, validate, generate` works from any cwd
- [x] CloudTrail `resources[]` fan-out works (closes the array-mapping gap)
      — 43 of 100 CloudTrail events now carry populated resource arrays

---

### Phase B — Minimal UI (~3–5 days) — ✅ DONE

**Status:** Shipped in commits `eed5922` (session 1: homepage + Sample +
Output tabs) and `eb13785` … `c6b97c7` (session 2: Mapping editor +
Validation + Coverage tabs + `/new` wizard). The original plan called for
four tabs; the shipped UI has five (Sample / Output / Mapping / Validation
/ Coverage).

**Goal:** A web app that lists mappings, lets a user upload a sample, and shows
the validated OCSF output.

#### Stack

- **FastAPI** for routes + **Jinja2** for HTML. No React/Vue.
- **HTMX** for interactivity (drop file → swap in result panel). No JS framework.
- **Monaco editor** via CDN for the JSON mapping editor.
- One-process app, file-backed (no database in v1).

#### Routes

| Path | Renders |
|---|---|
| `GET /` | homepage: card grid of all mappings |
| `GET /sources/{name}` | per-source view with tabs (mapping, sample, output, validation) |
| `POST /sources/{name}/apply` | take an uploaded file, return mapped OCSF events as JSON |
| `POST /sources/{name}/validate` | run validator, return issue list |
| `POST /sources/{name}/save` | save edited mapping (writes file, runs lint) |
| `GET /new` | new-mapping wizard (step 1: paste sample) |
| `POST /new/generate` | call `generate(sample, name)`, return draft mapping |

#### Homepage card content

```
┌─ <source name> ──────────────────────────┐
│ <OCSF class(es) targeted>                │
│ <N events validated> ✓  / ⚠  / ✗         │
│ <last modified, by whom>                 │
│ coverage: ███████████░░  78%             │
└──────────────────────────────────────────┘
```

#### Per-source page

Four tabs powered by HTMX swaps:

1. **Mapping** — Monaco JSON editor, inline DSL validation, save button (runs lint server-side, rejects on failure).
2. **Sample** — list of pinned sample files; "drop new" to add another.
3. **OCSF output** — pick a sample, see side-by-side: raw input | mapped OCSF | required-field heatmap.
4. **Validation** — full validator output (class constraints, required fields, dictionary type checks, basic sanity: timestamps in past, IPs well-formed).

#### Acceptance criteria

- [x] Homepage renders all mappings with live stats from disk — 29 cards
      with priority badge + coverage bar + lint pill
- [x] Drop a log file → see OCSF output rendered side-by-side within 1 s
- [x] Edit mapping JSON → save → fail save with red banner if lint fails
      (tmp-file + lint_one() gate; only promotes on `status == OK`)
- [x] Validator output shows specific failures, not just yes/no — separate
      Validation tab with a recurring-issues rollup
- [x] Works without any external services running (pure file-backed)

---

### Phase C — Generate-mapping wizard (~2 days) — ✅ DONE

**Goal:** UI flow that turns "I have a log file" into "I have a working mapping"
in under 5 minutes.

#### Wizard flow

1. **Step 1 — upload sample.** User drops a `.log` / `.jsonl` / `.csv` file. Up to 1 MB; first 10 lines are used for the LLM prompt.
2. **Step 2 — confirm source name + vendor.** Auto-suggest from filename.
3. **Step 3 — class selection.** `generate.suggest_classes(sample)` calls Claude phase 1. UI shows the suggested OCSF class(es) with a "why this class" explanation. User can override.
4. **Step 4 — draft mapping.** `generate.draft_mapping(sample, classes)` calls Claude phase 2. UI shows the JSON config in the Monaco editor with the sample → OCSF output rendered live next to it.
5. **Step 5 — save.** Writes to `mappings/<name>.json` + `samples/<name>.<ext>`, runs the lint, shows pass/fail. On pass, redirects to the source's detail page.

#### LLM safeguards

- Phase 1 prompt constrains the returned class names to the catalog (post-LLM check rejects unknown names).
- Phase 2 prompt includes only the schema for the chosen classes (keeps the prompt under ~50 KB).
- Post-LLM check: every `path` target in the output must exist in the OCSF dictionary or in a known nested object.
- The wizard never auto-saves a mapping that fails its own pinned sample.

#### Acceptance criteria

- [x] Onboard a new source from a fresh sample in < 5 min total — `/new`
      wizard takes minutes end-to-end (most time is the LLM round-trip)
- [x] Generated mapping validates against the schema on first save — the
      save endpoint runs `lint_one()` against the just-uploaded sample
      and refuses to write the file unless it passes
- [~] If LLM produces an invalid target path, the UI shows the offending
      lines and refuses to save — refuses to save: ✓ (red banner with the
      specific lint errors). "Highlight offending lines" in the Monaco
      editor: not implemented; falls back to the textual error list.

**Status:** Shipped as part of Phase B step 4 (commit `c6b97c7`). The
underlying generator and provider abstraction landed earlier in commit
`0b2d37e` (`ocsf-mapper generate <source> <sample>` CLI). Provider
auto-detection: `OCSF_LLM_PROVIDER` env → `ANTHROPIC_API_KEY` →
`OPENAI_API_KEY` → `RuntimeError`. A `fixture` provider reads canned
responses from `tests/fixtures/llm/<source>.json` for offline / CI use.

---

### Phase D — Polish (~ongoing) — 🔄 partial

Features that move it from "works" to "good":

- [x] **Coverage report**: per-mapping completeness score, weighted
      (required × 2 + recommended). Renders as colored bars on each card
      and on a dedicated Coverage tab with missing-attr lists.
      `src/ocsf_mapper/coverage.py`. Commit `8decc99`.
- [x] **Schema-bump diff**: `ocsf-mapper schema-diff [<ref>]` compares
      current schema against an older git ref of the submodule, joins
      per-class diffs (added required, at_least_one, activity enum)
      against mappings to surface silent breakage. Commit `01904ee`.
- [x] **Stream test mode (CLI)**: `ocsf-mapper tail <mapping> <file>` —
      `tail -f`-style polling, no third-party deps. Routes through any
      sink. Commit `99afe59`.
- [x] **Stream test mode (UI)**: per-source Output tab gains a "Live tail"
      toggle. Backend streams over SSE; events arrive in the browser as
      lines append to the source file. Commit `8cda605`.
- [x] **Export targets**: 5 sink kinds (`jsonl`, `csv`, `parquet`,
      `security-lake`, `stdout`) wired into the CLI's `apply` subcommand.
      `security-lake` writes `<root>/<class_uid>/eventDay=YYYYMMDD/*.parquet`
      for AWS Security Lake ingest. Commit `efec7e7`.
      UI export drop-down: not built.
- [x] **Mapping comparison**: see "Mapping comparison" entry below
      (under the perf series — it shipped as part of step 3 of the
      1→2→3→4 post-perf finish).
- [x] **PII redaction layer**: `RedactingSink` wraps any sink; scrubs
      email / ipv4 / ssn / phone / jwt / Luhn-valid ccn by default or a
      chosen subset. CLI: `apply ... --redact [kind ...]`.
      Commit `6de0d9d`.

### Performance series (added post-Phase-A in response to 10 TB scaling question)

Single-process apply ran ~2-6 KB/s in real terms; at 10 TB that was tens
of days. Five commits bring the local tool to a useful operating range:

- [x] **Regex cache + streaming input** (`6cccb62`): `lru_cache` on
      `re.compile` in `parse_record`; CLI iterates input line-by-line
      instead of `read_text().splitlines()`. Bounded input memory + ~1.5-2×
      on regex sources.
- [x] **orjson fast-path** (`619776b`): `pip install ocsf-mapper[fast]`
      pulls in `orjson`. `_fastjson.py` shim routes JSON parse/dump
      through orjson when available, stdlib otherwise. 5-10× per
      parse/dump call; ~2-5× overall on JSON-shaped sources.
- [x] **Streaming SecurityLakeSink + Parquet schema** (`522ed29`):
      `SecurityLakeSink(flush_every=50_000, schema=…)` rolls a fresh
      `part-NNNNN.parquet` per partition every N events. Memory bounded
      regardless of input size. `infer_schema_from(sample_event)` builds
      a `pa.Schema` so subsequent flushes skip type inference.
- [x] **Multiprocess apply** (`1b7039c`): `apply --workers N` splits the
      input by line-aligned byte ranges, fans out via
      `ProcessPoolExecutor`. JSONL/CSV/Parquet sinks get per-worker
      output files; SecurityLakeSink gets a per-worker `file_prefix`.
      Linear speedup to CPU count.
- [x] **Benchmark subcommand** (`8d4cdb5`): `ocsf-mapper benchmark
      <mapping> <sample>` reports per-phase wall time (parse / route /
      map / write) + events/sec + MB/sec. Made the surprising finding
      that **`map_record` dominates at ~90%** — the dictionary-walking
      cost of the DSL itself, not JSON parse. Means orjson alone moves
      the needle modestly; multiprocess fan-out + Security Lake
      streaming are the real wins at scale.

Combined: ~30-50× over the original single-threaded baseline. 10 TB on
an 8-core box: ~1-2 days, not ~40 days. Bounded memory. For larger
workloads, the tool is intended as a *mapping development* environment;
the JSON DSL config travels into a real distributed runtime
(Spark / Flink / Vector) for production.

- [x] **Mapping comparison**: `ocsf-mapper diff <a.json> <b.json>`
      reports metadata, parser, routing, and per-class changes — added
      / removed targets and op-kind / op-body changes for shared
      targets. ``--json`` for machine-readable output. Closes the
      original Phase D list.

---

## 4. DSL reference (frozen surface)

```jsonc
{
  "source_name": "<short_name>",
  "parser": "json"
          | { "regex": "<pattern>", "groups": ["..."] },
  "routing": {
    "field": "$.<source.path>",          // OR a regex group name
    "rules": [
      { "matches": ["v1", "v2"], "class": "<ocsf_class_name>" },
      { "prefix":  ["Create", "Put"], "class": "..." },
      { "default": true,              "class": "..." }
    ]
  },
  "classes": {
    "<ocsf_class_name>": {
      "mapping": {
        "<dotted.target.path>": <op>,
        ...
      }
    }
  }
}
```

See Phase A for the full list of ops.

### Conventions

- `metadata.version` must equal the bundled `ocsf-schema/version.json`.
- `metadata.product.name`, `metadata.product.vendor_name` must be set.
- `time` must be epoch ms (UTC).
- `type_uid` should always be `{"expr": "class_uid * 100 + activity_id"}`.
- `raw_data` must preserve the original input (use `{"raw": true}`).
- Pruning: null values and empty dicts are dropped at the end automatically.

---

## 5. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| DSL gaps push users to write Python escape hatches | Loses reviewability, breaks the SDK contract | Extend the DSL (already done 3× in prototype). Each new op is a small isolated change. |
| LLM hallucinates class names or attribute paths | Bad mappings reach prod | Post-LLM validators reject unknown names; UI shows offending lines. |
| Sample logs contain PII | Compliance / leak risk | Optional PII redaction layer; default-deny on storing samples with email/SSN patterns. |
| OCSF schema bumps break existing mappings | Silent drift | Schema-bump diff task in Phase D; CI lint catches new required fields. |
| `for_each` and other complex ops accumulate edge cases | DSL grows into a programming language | Cap at ~12 op kinds. Anything more complex → file an issue, not a new op. |

---

## 6. Resolved decisions

| # | Decision | Implication |
|---|---|---|
| 1 | **Local dev tool.** Runs on the user's machine. No multi-tenancy, no auth, no shared state. | FastAPI bound to `127.0.0.1` only. No DB. Filesystem is the source of truth. |
| 2 | **File export (recommended) — see §10.** Mapped events render in the UI and can be exported as JSONL / NDJSON / Parquet / CSV. Pluggable sink interface so S3/Splunk/OpenSearch can be added later without changing the core. | Phase B ships with JSONL export. Parquet via `pyarrow` (optional dep). Sink interface is part of the SDK. |
| 3 | **Both batch and stream — see §11.** Batch = upload a file in the UI or `ocsf-mapper apply` on the CLI. Stream = pipe stdin OR tail-a-file with live UI updates. | Phase A: batch + CLI stdin. Phase B: batch UI upload. Phase D: file-tail mode with WebSocket-pushed UI updates. |
| 4 | **`ocsf-schema` as a git submodule.** Frozen per release of `ocsf-parse`. Bumping the schema is an explicit PR with the lint as the gate. | `pip install -e .` clones the submodule. CI runs the lint after every schema bump to surface drift. |
| 5 | **User-provided LLM key — Anthropic OR OpenAI.** Abstract provider interface, auto-detect from env. See §12. | Phase A ships with `AnthropicProvider` and `OpenAIProvider`. `OCSF_LLM_PROVIDER` env var picks; otherwise auto-detect via `ANTHROPIC_API_KEY` then `OPENAI_API_KEY`. |

---

## 7. Output sinks (where mapped events go)

**Recommendation: ship JSONL + Parquet + CSV file export in v1. Add network
sinks (S3/Splunk/OpenSearch) only when a real consumer asks for them.**

For a local dev tool, the user mostly wants to inspect the output and move it
elsewhere by hand. A pluggable sink interface keeps that simple now without
foreclosing future options.

```python
from typing import Protocol, Iterator

class Sink(Protocol):
    def write_one(self, event: dict) -> None: ...
    def write_many(self, events: Iterator[dict]) -> int: ...
    def close(self) -> None: ...
```

### Built-in sinks (Phase A)

| Sink | What it produces | When to use |
|---|---|---|
| `JsonlSink(path)` | One OCSF JSON object per line | Default. Best for re-ingestion into any tool that reads NDJSON. |
| `CsvSink(path, flatten=True)` | Flattened OCSF (dotted column names) | Spreadsheets, ad-hoc grep. Lossy for nested objects. |
| `ParquetSink(path)` | Columnar Parquet (via `pyarrow`) | Direct compatibility with AWS Security Lake's expected format. Optional dep. |
| `StdoutSink()` | NDJSON to stdout | CLI piping. |

### Future sinks (only when asked)

- `S3ParquetSink(bucket, prefix)` — for direct Security Lake ingestion
- `SplunkHecSink(url, token)` — Splunk HTTP Event Collector
- `OpenSearchSink(url, index)` — for local Elasticsearch/OpenSearch dev clusters
- `KafkaSink(brokers, topic)` — streaming pipelines

### UI integration (Phase B)

The "Export" button on the per-source page offers JSONL / CSV / Parquet.
The actual export happens through `sinks/`, so the UI shares one code path
with the CLI.

---

## 8. Batch vs stream — both supported

Local dev tools should match how engineers actually work: sometimes you have
a file, sometimes you have a live tail.

### Batch (Phase A + B)

**CLI:**
```bash
# explicit file
ocsf-mapper apply mappings/nginx.json access.log out.jsonl

# stdin pipe (composes with anything Unix)
cat access.log | ocsf-mapper apply mappings/nginx.json - > out.jsonl

# from a remote source
kubectl logs -f deploy/api | ocsf-mapper apply mappings/k8s.json - | ...
```

**UI:**
Drop file in the per-source page → see OCSF output rendered side-by-side →
"Export" → JSONL / CSV / Parquet.

This covers ~90% of dev use cases. Files up to a few GB are fine because
everything is line-by-line iterators — no full-file load.

### Stream (Phase D)

**CLI (tail mode):**
```bash
ocsf-mapper tail mappings/nginx.json /var/log/nginx/access.log | jq .

# with sink
ocsf-mapper tail mappings/nginx.json /var/log/nginx/access.log \
  --sink parquet --out /tmp/nginx.parquet
```

Backed by `watchdog` (cross-platform file watching). On new lines, the SDK
emits OCSF events to whatever sink is configured.

**UI (live mode):**

- Per-source page gets a "Live tail" toggle.
- Backend opens a WebSocket; server tails the file via `watchdog`, pushes
  each new mapped event.
- UI renders a streaming feed with the last N events, plus a "freeze" button
  to inspect a specific event without missing new ones.
- Useful for "is my mapping actually working on the live log?"

### Why both

| Mode | Use case |
|---|---|
| Batch (file) | Iterating on a mapping with a captured sample. Safe to retry. |
| Batch (stdin) | Composing with Unix pipelines. Shipping events to a sink. |
| Stream (tail) | Verifying a mapping works on live production logs. |
| Stream (UI live) | Demo-friendly. "Show me events as they happen." |

The SDK has one core primitive — `apply_stream(config, lines_iter)` — and all
four modes are different sources for `lines_iter`. No mode-specific code paths.

---

## 9. LLM provider abstraction

Users bring their own key. Either Anthropic or OpenAI works.

### Interface

```python
from typing import Protocol

class LLMProvider(Protocol):
    name: str            # "anthropic" or "openai"
    default_model: str
    def complete(self, prompt: str, system: str = "",
                 max_tokens: int = 8000) -> str: ...
```

### Implementations

```python
class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-opus-4-7"          # large context, best JSON adherence
    # uses anthropic.Anthropic().messages.create()

class OpenAIProvider(LLMProvider):
    name = "openai"
    default_model = "gpt-4o"                   # JSON mode supported
    # uses openai.OpenAI().chat.completions.create(
    #   response_format={"type": "json_object"})
```

### Selection logic

```python
def get_provider() -> LLMProvider:
    explicit = os.environ.get("OCSF_LLM_PROVIDER")  # "anthropic" | "openai"
    if explicit == "anthropic": return AnthropicProvider()
    if explicit == "openai":    return OpenAIProvider()

    # Auto-detect: prefer Anthropic if both keys present
    if os.environ.get("ANTHROPIC_API_KEY"): return AnthropicProvider()
    if os.environ.get("OPENAI_API_KEY"):    return OpenAIProvider()

    raise RuntimeError(
        "No LLM key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, "
        "or use OCSF_LLM_PROVIDER to choose explicitly."
    )
```

### Per-provider prompt tweaks

The two phases (class selection, mapping generation) share 95% of the prompt
across providers. The differences:

- **Anthropic** — `system="You produce strict JSON, no commentary."` Works
  reliably on Opus 4.7.
- **OpenAI** — same system prompt **plus** `response_format={"type": "json_object"}`
  to force JSON. The user prompt must include the word `JSON` for OpenAI's
  JSON mode to be enabled.

Both providers see the same prompt text from `generate.py`. Provider-specific
quirks live in the provider class, not the prompt builder.

### Cost notes (informational, not enforced)

| Provider | Onboarding cost per source (≈) |
|---|---|
| Anthropic Opus 4.7 | $0.05 – $0.20 (50 KB prompt, two phases) |
| OpenAI GPT-4o | $0.02 – $0.08 |

Either way: trivial compared to writing a parser by hand.

### Optional: dry-run / cached mode

For offline development and CI, support `OCSF_LLM_PROVIDER=fixture` which
reads canned LLM responses from `tests/fixtures/llm/<source>.json`. Lets the
test suite cover the generation flow without spending API tokens.

---

## 10. What's already built — *historical, Phase A port complete*

The original prototype lived at `/tmp/ocsf-demo/` and was the starting point
for the SDK port. Every file in the table below has been moved into the
package and is now live; this section is kept for archaeology.

| File | Status | Moved to |
|---|---|---|
| `apply_mapping.py` (DSL executor, 175 lines, 11 op kinds) | Ported | `src/ocsf_mapper/apply.py` + split `ops.py` |
| `validate_ocsf.py` (schema validator) | Ported | `src/ocsf_mapper/validate.py` |
| `gen_mapping.py` (LLM generator, SDK-wired) | Ported & refactored behind a provider abstraction | `src/ocsf_mapper/generate.py` + `providers/` |
| `lint_mappings.py` (CI gate) | Ported | `scripts/lint_mappings.py` + `src/ocsf_mapper/lint.py` |
| 4 reference mappings (nginx, cloudtrail, okta, palo_alto) | Ported, then grew to 29 | `mappings/` |
| 4 sample inputs | Ported, regenerated to ~100 events each | `samples/` |
| Anthropic SDK wiring + mock smoke test | Ported; covered by `FixtureProvider` for offline tests | `tests/test_providers.py`, `tests/test_generate.py` |

Beyond the original list, the following were added during Phase A:
**11-op DSL** (added `for_each`), **`epoch_s` time format**, **CLI with
10 subcommands** (`apply`, `validate`, `list`, `catalog`, `lint`,
`schema-diff`, `benchmark`, `generate`, `tail`, `serve`), **5 sink kinds**,
**catalog.json master data**, **GitHub Actions CI on 3.9 / 3.11 / 3.12**,
**deterministic sample generator**.

---

## 11. Order of operations — *historical*

The original plan: Phase A first, then B and D-polish in parallel, with
C blocking on B's per-source page. **All four phases shipped in this order
in practice.** The branch's commit log mirrors the planned sequence
(`6c4070a` Phase-A port → `bffc0c8` CLI/sinks/CI → `0b2d37e` for_each +
LLM abstraction → `eed5922` Phase-B session 1 → `eb13785`…`c6b97c7`
Phase-B session 2 / Phase-C wizard → `efec7e7` `99afe59` Phase-D polish).

---

## 12. Open work (post-Phase-A/B/C)

The repo has crossed the "feature-complete for v0.1" line. Remaining items
called out in this plan but not yet shipped:

- **Phase D / Schema-bump diff** — when the `ocsf-schema` submodule is
  bumped, surface "this mapping is missing newly-required field X on
  class Y" so we can fix mappings before lint fails on the next PR.
  Estimated: half a session.
- **Phase D / Stream test mode (UI)** — Output tab gets a "Live tail"
  toggle that pushes mapped events over SSE or WebSocket. Backend reuses
  the existing `stream_apply` helper. Estimated: half a session.
- **Phase D / Mapping comparison** — side-by-side diff of two mappings
  (e.g. two variants of the same vendor's logs). Estimated: half a
  session.
- **Phase D / PII redaction** — opt-in sink wrapper that strips configured
  fields (or matches known PII patterns: email, SSN) before write.
  Estimated: half a session.
- **Phase D / Coverage badges in CI** — emit `coverage.json` from
  `lint_mappings` and post a per-PR comment showing the coverage delta.
  Estimated: quarter session.
- **Mappings beyond OCSF base** — Windows-extension classes
  (`registry_key_activity` etc) require loading `ocsf-schema/extensions/`,
  which the validator doesn't do yet. Estimated: one session of
  schema-loader work.
- **OCSF categories not yet covered** — `remediation` (category 7)
  and `unmanned_systems` (category 8). No representative source proposed
  yet.

For the active acceptance criteria and feature list see the Status block
at the top of this file and [`CHANGELOG.md`](./CHANGELOG.md).
