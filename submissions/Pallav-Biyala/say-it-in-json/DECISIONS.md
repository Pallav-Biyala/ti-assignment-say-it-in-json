# Decisions — Say It in JSON

Target format: **`pfcfg-json/v1`**. This note records the semantic contract for converter, evaluators, and verifier. Starter configs in `starter/configs/` take precedence if they conflict with the incomplete wiki excerpt.

## 1. JSON schema design and tradeoffs

### Chosen: ordered statement AST

Each source `.pfcfg` file converts to a corresponding `.json` file (same relative layout):

```json
{ "format": "pfcfg-json/v1", "source": "<repo-relative .pfcfg path>", "body": [ Stmt, ... ] }
```

Statements (`op`, in application order):

| `op` | role |
| --- | --- |
| `include` / `include_once` | load sibling `.json` document; path relative to containing file |
| `ifdef` / `ifndef` | check env var only; nested `body: Stmt[]` |
| `set` | explicit `section`, `key`, `value` (Value node below) |

Values are typed nodes so nested interpolation is unambiguous:

| Shape | Meaning |
| --- | --- |
| `{ "lit": "<string>" }` | literal (booleans/numbers/lists stay strings in v1) |
| `{ "env": "<VAR>" }` | `${VAR}` (emit migration-risk diagnostic — no default) |
| `{ "env": "<VAR>", "default": Value }` | `${VAR:-...}` |
| `{ "env": "<VAR>", "alternate": Value }` | `${VAR:+...}` |
| `{ "ref": { "section", "key" } }` | `$(section.key)` — dotted sections allowed |
| `{ "concat": [ Value, ... ] }` | ordered concatenation |

Formal JSON Schema: `solution/schema/pfcfg-json-v1.schema.json` (Draft 2020-12).

### Rejected

- **Nested section objects.** Cannot encode statement-order last-wins (Globex interleaves includes, `@ifdef PRODUCTION` / `@ifndef PRODUCTION`, and sets; Acme overwrites `deploy.requires_approval`). JSON object key ordering is not a semantic guarantee.
- **Resolved snapshot JSON.** A flattened `{ "section.key": "resolved-value" }` cannot be re-evaluated under a different environment. The whole point of the migration is that JSON is independently evaluable.
- **Magic keys like `$ifdef`, `$include` inside objects.** Collision risk with real section/key names.

## 2. "Effective settings" definition

After evaluation, a configuration is a tuple of:

1. **Resolved map:** `dict[(section, key) -> str]` — fully expanded.
2. **Unresolved map:** `dict[(section, key) -> str]` — keys that failed to expand (circular ref, expansion-pass-limit, or undefined cross-ref target). Contains an error marker string so the verifier can compare unresolved-state parity.
3. **Diagnostic report.** Severity + code + file/section/key/line/details for each problematic construct.

### Ordering interpretation (matches starter observed behaviour)

- Statements process top-to-bottom. Includes/conditionals fire **when encountered** (not a separate pre-processing pass).
- `@ifdef/@ifndef` check **process env only** (set and non-empty vs unset-or-empty). They never inspect config keys.
- **Last wins.** Same `(section, key)` set twice: the later `set` wins. Applied across includes, inside or outside conditional blocks. Exercised by Acme's `deploy.requires_approval` (default `true`, under `@ifdef ACME_DEPLOY_TARGET` → `false`).
- `@include_once` skipped iff the **canonical resolved JSON path** was already loaded in this evaluation (per top-level entry).
- **Expansion passes:** After merge, `env`/`ref`/`concat` expand to a fixpoint (max 100 passes). Circular `ref` graphs and expansion-limit overflow are **errors** (not silently dropped keys).

## 3. What the verifier proves — and does not prove

**Proved, for every (entry config, fixture) pair in the provided matrix (5 entries × 4 fixtures = 20 runs):**
- Key-set parity between legacy-eval and JSON-eval outputs.
- Per-key resolved value string equality (or unresolved error-marker string equality).
- The verifier is **not vacuous.** Three tests deliberately tamper with converted JSON (value changed, key deleted, key injected) and assert `result.passed == False`.

**NOT proved:**
- Mathematical/formal equivalence over all possible `.pfcfg` inputs.
- Equivalence over environments not covered by the four fixtures (e.g., partial CI flag combinations, every possible combination of `_ALTERNATE` unset, etc.).
- Behaviour with include graphs deeper than 6 levels (starter uses ~3).
- The 100-pass expansion limit is empirical; the wiki says an "expansion pass limit" exists but the exact number is disputed.

## 4. Known gaps

- `$(section.key)` uses the post-merge **global last-wins map** only. The wiki does not describe file-scoped references, and no starter config tests that ambiguity.
- The JSON evaluator rewrites include paths ending in `.pfcfg` → `.json` as a safety net. A strictly correct conversion already writes `.json` into the include path; this is defensive.
- `jsonschema` package is optional. Without it, schema tests do structural hand-rolled checks only. With it, every produced document is fully validated.

## 5. What I would build next with four more hours

1. **Per-config fuzz harness.** Property-based round-trip: generate random `.pfcfg`-like text with includes/conditionals/interpolation, convert, dual-evaluate, assert-equivalent. Catches edge cases the hand-written starter doesn't exercise.
2. **Configurable expansion limit + pass diagnostics.** Emit a diagnostic *every* pass count (currently silent until failure) so a human can see a config is at 98/100 passes and likely brittle.
3. **Diff-friendly migration report.** Human-readable HTML or rich terminal report grouping diagnostics by `file → severity → code`. Currently JSON/NDJSON only.
4. **`--strict` verifier mode.** Fail on ANY warning (not only errors/mismatches). Useful for production roll-out gating.
