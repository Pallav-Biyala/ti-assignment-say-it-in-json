# pfcfg-json/v1 — PipelineForge Config Migration Solution

Thin vertical slice: schema + converter + two independent evaluators +
equivalence verifier + unmigratable diagnostics.

## What's in the box

```
solution/
├── pfcfg_json/
│   ├── __init__.py          # Re-exports all public APIs
│   ├── __main__.py          # python -m solution.pfcfg_json ...
│   ├── model.py             # pfcfg-json/v1 AST + JSON serialize/deserialize
│   ├── parser.py            # .pfcfg text → AST parser (existing, 24/24 tests)
│   ├── diagnostics.py       # Diagnostic codes, Severity, DiagnosticReport
│   ├── evaluator_legacy.py  # Reference .pfcfg evaluator (computes effective settings)
│   ├── evaluator_json.py    # INDEPENDENT JSON evaluator (never reads .pfcfg)
│   ├── converter.py         # .pfcfg tree → pfcfg-json/v1 JSON tree
│   ├── verifier.py          # Legacy-vs-JSON equivalence verifier (FAILS on mismatch)
│   ├── fixtures.py          # ci / non_ci / production / minimal env fixtures
│   └── cli.py               # convert / verify / report / list-fixtures CLI
├── schema/
│   └── pfcfg-json-v1.schema.json  # Formal JSON Schema (Draft 2020-12)
└── tests/
    ├── test_parser.py       # Existing parser + AST tests (24 cases)
    └── test_full.py         # Full suite: diagnostics + both evaluators +
                             # converter + verifier (incl. tamper-detection) +
                             # fixtures + schema + CLI smoke tests
```

## Setup

No third-party runtime dependencies. Standard library only.

Optional: `pip install jsonschema` for formal JSON Schema validation (the test
suite runs structural checks without it and notes the limitation honestly).

Run from the **repository root** (`e:\Linux\ti-assignment-say-it-in-json\`).

## Quick start (≤ 15 minutes on a laptop)

### 1. Convert all starter configs

```
python -m solution.pfcfg_json convert \
    --source starter/configs \
    --output /tmp/pfcfg-json-out \
    --report /tmp/diag-convert.json
```

Produces one `.json` per `.pfcfg` (same tree layout) under the output dir plus a
diagnostics report listing unmigratable and risky constructs.

### 2. Run equivalence verification

```
python -m solution.pfcfg_json verify \
    --source starter/configs \
    --json-root /tmp/pfcfg-json-out \
    --report /tmp/verify-report.json
```

Runs every required entry config against all four environment fixtures:

| Fixture | Purpose |
| --- | --- |
| `ci` | `CI` set, most vars populated — exercises CI-active conditionals |
| `non_ci` | `CI` empty — exercises defaults and `@ifndef CI` |
| `production` | `CI` + `PRODUCTION` set — exercises Globex on-prem overlay |
| `minimal` | Only the strictly-required env-no-default vars — maximises defaults |

Expected result for the starter tree: **ALL PASSED** over the 5 entries × 4
fixtures (20 runs).

### 3. Unmigratable / risky report

```
python -m solution.pfcfg_json report \
    --source starter/configs \
    --format ndjson \
    --output /tmp/unmigratable.ndjson
```

NDJSON is one diagnostic per line, easy to pipe into downstream tooling.

### 4. List fixtures

```
python -m solution.pfcfg_json list-fixtures -v
```

## Running the test suite

From the repo root:

```
python -m unittest solution.tests.test_parser -v     # 24 existing parser tests
python -m unittest solution.tests.test_full -v       # New comprehensive suite
```

Or discover everything under `solution/tests/`:

```
python -m unittest discover -s solution/tests -v
```

## Core architecture decisions (summary — see `../DECISIONS.md` for full)

- **JSON is an ordered AST**, not a nested object. Includes, conditionals, and
  last-wins assignments are statement-order dependent. Object key ordering is
  not a semantic JSON guarantee.
- **Two independent evaluators.** `evaluator_legacy.py` uses `.pfcfg` → AST →
  effective-settings. `evaluator_json.py` uses `.json` → AST →
  effective-settings, and **never imports the .pfcfg parser**. The verifier
  compares their outputs per-key.
- **Verifier is not vacuous.** It has tests that deliberately tamper with the
  converted JSON (change a value, delete a key, add a spurious key) and assert
  `result.passed == False`.
- **Expansion pass limit is 100.** Circular `$(section.key)` references are
  errors, not silent output.
- **JSON includes point at `.json`.** The converter translates `.pfcfg`
  `@include x/y.pfcfg` into JSON `include` of `x/y.json`.

## What equivalence means (honest)

The verifier demonstrates that, for each entry config × each provided fixture,
the legacy and JSON evaluators agree on:
1. The set of `(section, key)` pairs produced.
2. The **resolved** string value of each pair.
3. The **unresolved/error** state of each pair (and the error marker string).

It does **not** prove mathematical equivalence over all inputs, all environments,
or all include graphs. It proves equivalence over the concrete starter config
tree and four representative fixtures. New customer configs must be added to
the fixture matrix and re-verified.

## Known gaps

- No streaming parser for huge `.pfcfg` files (the starter is small).
- Expansion limit of 100 is a constant; not per-config or configurable via
  schema.
- `$(section.key)` references always use the **last-wins fully-merged flat map**
  — per-file scoped references are not modelled, matching the starter observed
  behaviour.
- `jsonschema` package is optional; the test suite notes when it's not
  installed and falls back to structural checks.
