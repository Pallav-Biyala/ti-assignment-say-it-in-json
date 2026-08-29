"""Command-line interface for the pfcfg-json/v1 toolchain.

Commands:
    convert  - Convert a .pfcfg tree to pfcfg-json/v1 JSON
    verify   - Run equivalence verification between legacy and JSON evaluators
    report   - Emit the unmigratable/risky diagnostics report

Run with:
    python -m solution.pfcfg_json.cli <command> [options]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .diagnostics import DiagnosticReport, Severity
from .fixtures import (
    ENTRY_CONFIGS_RELATIVE,
    FIXTURE_INFO,
    FIXTURES,
    all_fixture_names,
    get_fixture,
)
from .converter import convert_tree
from .verifier import verify_entry


def _cmd_convert(args: argparse.Namespace) -> int:
    src = Path(args.source).resolve()
    dst = Path(args.output).resolve()
    if not src.is_dir():
        print(f"error: source {src} is not a directory", file=sys.stderr)
        return 2

    entry_points = args.entry if args.entry else ENTRY_CONFIGS_RELATIVE
    result = convert_tree(src, dst, env={}, entry_points=entry_points)

    print(f"Converted {len(result.files_converted)} file(s).")
    for pfcfg, json_path in result.files_converted:
        print(f"  {pfcfg}  ->  {json_path}")
    summary = result.report.summary()
    print(f"Diagnostics: {summary['total']} total "
          f"({summary['error']} error, {summary['warning']} warning, "
          f"{summary['unmigratable']} unmigratable)")
    if args.report:
        report_path = Path(args.report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if args.format == "ndjson":
            report_path.write_text(result.report.to_ndjson(), encoding="utf-8")
        else:
            report_path.write_text(
                json.dumps(result.report.to_list(), indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"Diagnostic report written to {report_path}")
    return 0 if not result.report.has_errors else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    src_root = Path(args.source).resolve()
    json_root = Path(args.json_root).resolve()
    if not src_root.is_dir():
        print(f"error: source config root {src_root} is not a directory", file=sys.stderr)
        return 2
    if not json_root.is_dir():
        print(f"error: converted JSON root {json_root} is not a directory — run 'convert' first?", file=sys.stderr)
        return 2

    entries = args.entry if args.entry else ENTRY_CONFIGS_RELATIVE
    fixture_names = args.fixture if args.fixture else all_fixture_names()

    all_passed = True
    summary: list[dict[str, object]] = []

    for entry_rel in entries:
        pfcfg_path = (src_root / entry_rel).resolve()
        json_path = (json_root / entry_rel).with_suffix(".json").resolve()
        if not pfcfg_path.is_file():
            print(f"SKIP {entry_rel}: .pfcfg not found at {pfcfg_path}", file=sys.stderr)
            continue
        if not json_path.is_file():
            print(f"SKIP {entry_rel}: .json not found at {json_path} (run convert?)", file=sys.stderr)
            continue
        for fixture_name in fixture_names:
            env = get_fixture(fixture_name)
            result = verify_entry(pfcfg_path, json_path, env=env, env_name=fixture_name)
            status = "PASS" if result.passed else "FAIL"
            line = (f"[{status}] {entry_rel}  fixture={fixture_name}  "
                    f"missing_json={len(result.missing_in_json)}  "
                    f"missing_legacy={len(result.missing_in_legacy)}  "
                    f"value_mismatch={len(result.value_mismatches)}  "
                    f"unresolved_mismatch={len(result.unresolved_mismatches)}")
            print(line)
            if not result.passed:
                all_passed = False
                for mm in result.value_mismatches:
                    print(f"    VALUE MISMATCH [{mm.section}.{mm.key}]: "
                          f"legacy={mm.legacy_value!r}  json={mm.json_value!r}")
                for s, k in result.missing_in_json:
                    print(f"    MISSING IN JSON: [{s}.{k}]")
                for s, k in result.missing_in_legacy:
                    print(f"    EXTRA IN JSON: [{s}.{k}]")
            summary.append(result.to_dict())

    if args.report:
        report_path = Path(args.report).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nVerification report written to {report_path}")

    print("\n=== OVERALL ===")
    if all_passed:
        print("ALL PASSED")
        return 0
    print("SOME FAILED")
    return 1


def _cmd_report(args: argparse.Namespace) -> int:
    src = Path(args.source).resolve()
    dst = Path(args.output).resolve() if args.output else None
    if not src.is_dir():
        print(f"error: source {src} is not a directory", file=sys.stderr)
        return 2

    conversion_root = src.parent / "converted-json"

    result = convert_tree(
        src,
        conversion_root,
        entry_points=ENTRY_CONFIGS_RELATIVE,
    )
    items = result.report.to_list()

    if args.format == "ndjson":
        text = result.report.to_ndjson()
    else:
        text = json.dumps(items, indent=2) + "\n"

    if args.output:
        out = Path(args.output).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Unmigratable/risky report ({len(items)} items) written to {out}")
    else:
        sys.stdout.write(text)

    s = result.report.summary()
    print(f"\nTotal diagnostics: {s['total']}  errors={s['error']}  warnings={s['warning']}  unmigratable={s['unmigratable']}",
          file=sys.stderr)
    return 0 if s["error"] == 0 else 1


def _cmd_list_fixtures(args: argparse.Namespace) -> int:
    for name in all_fixture_names():
        desc = FIXTURE_INFO.get(name, "")
        print(f"{name}: {desc}")
        env = get_fixture(name)
        if args.verbose:
            for k, v in sorted(env.items()):
                marker = " (set)" if v else " (empty/unset-like)"
                print(f"    {k}={v!r}{marker}")
            print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pfcfg-json",
        description="Convert PipelineForge .pfcfg files to pfcfg-json/v1 JSON, and verify equivalence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("convert", help="Convert a .pfcfg tree to pfcfg-json/v1 JSON files")
    pc.add_argument("--source", required=True, help="Directory containing .pfcfg files (e.g. starter/configs)")
    pc.add_argument("--output", required=True, help="Output directory for .json files")
    pc.add_argument("--entry", action="append", help="Entry config relative to --source (repeatable). Defaults to the 5 starter entries.")
    pc.add_argument("--report", help="Optional path to write conversion diagnostics (JSON)")
    pc.add_argument("--format", choices=["json", "ndjson"], default="json", help="Report format (default: json)")
    pc.set_defaults(func=_cmd_convert)

    pv = sub.add_parser("verify", help="Run equivalence verification between .pfcfg and converted JSON")
    pv.add_argument("--source", required=True, help="Directory containing original .pfcfg files")
    pv.add_argument("--json-root", required=True, help="Directory containing converted .json files")
    pv.add_argument("--entry", action="append", help="Entry config relative path (repeatable)")
    pv.add_argument("--fixture", action="append", help="Environment fixture name (repeatable)")
    pv.add_argument("--report", help="Optional path to write per-run verification JSON report")
    pv.set_defaults(func=_cmd_verify)

    pr = sub.add_parser("report", help="Produce the unmigratable/risky diagnostics report for a config tree")
    pr.add_argument("--source", required=True, help="Directory containing .pfcfg files")
    pr.add_argument("--output", help="Output file path (stdout if omitted)")
    pr.add_argument("--format", choices=["json", "ndjson"], default="json", help="Report format (default: json)")
    pr.set_defaults(func=_cmd_report)

    pl = sub.add_parser("list-fixtures", help="List available environment fixtures and their variables")
    pl.add_argument("-v", "--verbose", action="store_true", help="Show individual env vars")
    pl.set_defaults(func=_cmd_list_fixtures)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
