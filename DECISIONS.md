# Decisions — Say It in JSON

Target format: **`pfcfg-json/v1`**. This note records the semantic contract for converter, evaluators, and verifier. Starter configs in `starter/configs/` take precedence if they conflict with the incomplete wiki excerpt.

## JSON representation

Each source `.pfcfg` file converts to a corresponding `.json` file (same relative layout). A document is:

```text
{ "format": "pfcfg-json/v1", "source": "<repo-relative .pfcfg path>", "body": [ Stmt, ... ] }
```

The body is an **ordered statement list** (AST), not a nested object of sections. Statement order is preserved because includes, conditionals, and assignments are applied top-to-bottom with sequential last-wins.

Statements:

| `op` | Role |
| --- | --- |
| `include` | Load another **JSON** document. `path` is relative to the directory of the file containing the directive. |
| `include_once` | Same, skipped if that file’s **canonical resolved JSON path** was already loaded in this top-level evaluation. |
| `ifdef` / `ifndef` | `var` is a process environment name; `body` is nested `Stmt[]`. |
| `set` | Assignment: explicit `section` and `key`. Section names may contain dots (`toolchain.node`). Reopening a section adds or overwrites keys; it does not replace the section. |

JSON includes point only at JSON files, never `.pfcfg`. The JSON evaluator must not read `.pfcfg`. A missing JSON include is an error.

Values are typed nodes so nested interpolation is unambiguous:

- `{ "lit": "<string>" }` — literal; `true`, `false`, `90`, and comma-separated lists stay strings in v1.
- `{ "env": "<VAR>" }` — `${VAR}`; unset or empty → empty string. Also emit a **migration-risk** diagnostic when there is no default.
- `{ "env": "<VAR>", "default": Value }` — `${VAR:-...}` (nested `Value` allowed).
- `{ "env": "<VAR>", "alternate": Value }` — `${VAR:+...}` (nested `Value` allowed).
- `{ "ref": { "section", "key" } }` — `$(section.key)`; chained expansion after merge.
- `{ "concat": [ Value, ... ] }` — concatenation.

Comments, original quoting, and `[section]`-then-keys grouping do not round-trip. Semantic equivalence of effective settings is the goal.

## Evaluation

- Process statements in order. Includes and conditionals run **when encountered**.
- `@ifdef` / `@ifndef` inspect the **process environment** only (set and non-empty vs unset or empty), never config keys.
- After the last-wins map is built, expand `env` / `ref` / `concat` to a fixpoint. Circular `ref` graphs and expansion-pass-limit failures are **errors**, not silently omitted keys.

## Why an ordered AST, not nested JSON

Nested objects cannot encode sequential last-wins. Globex interleaves includes, `@ifdef PRODUCTION` / `@ifndef PRODUCTION`, and later `set`s; Acme assigns `deploy.requires_approval` and then overwrites it under `@ifdef ACME_DEPLOY_TARGET`. JSON object key order is not a semantic guarantee, and special keys such as `$ifdef` collide with section names.

A resolved snapshot is not a migrated config: it cannot be re-evaluated under another environment. An ordered AST is independently evaluable from JSON alone and matches the `.pfcfg` processing model.
