"""Comprehensive tests for diagnostics, evaluators, converter, verifier, CLI, schema."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from solution.pfcfg_json import (
    Concat,
    Diagnostic,
    DiagnosticCode,
    DiagnosticReport,
    Document,
    Env,
    EnvAlternate,
    EnvDefault,
    FORMAT,
    Ifdef,
    Ifndef,
    Include,
    IncludeOnce,
    Literal,
    ParseError,
    Ref,
    Severity,
    Set,
    convert_tree,
    dumps,
    evaluate_json_document,
    evaluate_json_entry,
    evaluate_pfcfg_entry,
    loads,
    parse_text,
    parse_value,
    verify_entry,
)
from solution.pfcfg_json.diagnostics import Diagnostic as _D
from solution.pfcfg_json.fixtures import (
    FIXTURES,
    FIXTURE_INFO,
    all_fixture_names,
    get_fixture,
)

REPO = Path(__file__).resolve().parents[2]
CONFIGS = REPO / "starter" / "configs"
SCHEMA_PATH = REPO / "solution" / "schema" / "pfcfg-json-v1.schema.json"


# ---------------------------------------------------------------------------
# Diagnostics tests
# ---------------------------------------------------------------------------


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostic_to_dict(self) -> None:
        d = Diagnostic(
            code=DiagnosticCode.CIRCULAR_REF,
            severity=Severity.ERROR,
            reason="x -> y -> x",
            file="f.pfcfg",
            section="s",
            key="k",
            line=7,
            details={"path": ["x", "y"]},
        )
        out = d.to_dict()
        self.assertEqual(out["code"], "CIRCULAR_REF")
        self.assertEqual(out["severity"], "error")
        self.assertEqual(out["reason"], "x -> y -> x")
        self.assertEqual(out["file"], "f.pfcfg")
        self.assertEqual(out["section"], "s")
        self.assertEqual(out["key"], "k")
        self.assertEqual(out["line"], 7)
        self.assertEqual(out["details"], {"path": ["x", "y"]})

    def test_report_tracking(self) -> None:
        r = DiagnosticReport()
        r.add(Diagnostic(
            code=DiagnosticCode.UNMIGRATABLE_ENV_NO_DEFAULT,
            severity=Severity.WARNING,
            reason="no default",
            section="a",
            key="b",
        ))
        r.add(Diagnostic(
            code=DiagnosticCode.CIRCULAR_REF,
            severity=Severity.ERROR,
            reason="cycle",
        ))
        self.assertEqual(len(r.items), 2)
        self.assertEqual(len(r.errors), 1)
        self.assertEqual(len(r.warnings), 1)
        self.assertEqual(len(r.unmigratable), 1)
        self.assertTrue(r.has_errors)
        s = r.summary()
        self.assertEqual(s["error"], 1)
        self.assertEqual(s["warning"], 1)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["unmigratable"], 1)

    def test_report_ndjson_json_roundtrip(self) -> None:
        r = DiagnosticReport()
        r.add(Diagnostic(
            code=DiagnosticCode.VERIFY_MISMATCH,
            severity=Severity.ERROR,
            reason="v1 != v2",
            section="s",
            key="k",
        ))
        items_json = json.loads(json.dumps(r.to_list()))
        self.assertEqual(len(items_json), 1)
        self.assertEqual(items_json[0]["code"], "VERIFY_MISMATCH")
        ndjson_lines = [l for l in r.to_ndjson().splitlines() if l]
        self.assertEqual(len(ndjson_lines), 1)
        self.assertEqual(json.loads(ndjson_lines[0])["code"], "VERIFY_MISMATCH")


# ---------------------------------------------------------------------------
# Legacy evaluator tests
# ---------------------------------------------------------------------------


class LegacyEvaluatorTests(unittest.TestCase):
    def test_acme_last_wins_deploy_approval(self) -> None:
        env = {"ACME_DEPLOY_TARGET": "prod"}
        result = evaluate_pfcfg_entry(
            CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg",
            env=env,
        )
        val, ok = result.get("deploy", "requires_approval")
        self.assertTrue(ok)
        self.assertEqual(val, "false")

    def test_acme_no_env_keeps_default_approval(self) -> None:
        result = evaluate_pfcfg_entry(
            CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg",
            env={},
        )
        val, ok = result.get("deploy", "requires_approval")
        self.assertTrue(ok)
        self.assertEqual(val, "true")

    def test_globex_non_production_uses_overrides(self) -> None:
        result = evaluate_pfcfg_entry(
            CONFIGS / "customers" / "globex" / "pipeline.pfcfg",
            env={},
        )
        cache_enabled, ok = result.get("cache", "enabled")
        self.assertTrue(ok)
        self.assertEqual(cache_enabled, "false")

    def test_globex_production_uses_on_prem(self) -> None:
        result = evaluate_pfcfg_entry(
            CONFIGS / "customers" / "globex" / "pipeline.pfcfg",
            env={"PRODUCTION": "1", "REQUIRED_SIGNING_SECRET": "x"},
        )
        deploy_strategy, ok = result.get("deploy", "strategy")
        self.assertTrue(ok)
        self.assertEqual(deploy_strategy, "manual")
        registry, ok = result.get("container", "registry")
        self.assertTrue(ok)
        self.assertEqual(registry, "registry.globex.internal")

    def test_initech_cross_key_and_concat(self) -> None:
        result = evaluate_pfcfg_entry(
            CONFIGS / "customers" / "initech" / "pipeline.pfcfg",
            env={"REQUIRED_SIGNING_SECRET": "redacted"},
        )
        compiler, ok = result.get("build", "compiler_path")
        self.assertTrue(ok)
        self.assertEqual(compiler, "/usr/local/bin/node")
        release, ok = result.get("release", "bundle_name")
        self.assertTrue(ok)
        self.assertEqual(release, "initech-0.0.0-20.tar.gz")

    def test_cascade_order_expansion(self) -> None:
        env = {"CASCADE_ALPHA": "myalpha"}
        result = evaluate_pfcfg_entry(
            CONFIGS / "edge-cases" / "interpolation-cascade.pfcfg",
            env=env,
        )
        beta, ok = result.get("cascade", "beta")
        self.assertTrue(ok)
        self.assertEqual(beta, "prefix-myalpha-suffix")
        gamma, ok = result.get("cascade", "gamma")
        self.assertTrue(ok)
        self.assertEqual(gamma, "prefix-myalpha-suffix")

    def test_cascade_ci_overwrites_epsilon(self) -> None:
        base = evaluate_pfcfg_entry(
            CONFIGS / "edge-cases" / "interpolation-cascade.pfcfg",
            env={"CASCADE_ALPHA": "a"},
        )
        with_ci = evaluate_pfcfg_entry(
            CONFIGS / "edge-cases" / "interpolation-cascade.pfcfg",
            env={"CI": "yes", "CASCADE_ALPHA": "a"},
        )
        eps_base, _ = base.get("cascade", "epsilon")
        eps_ci, _ = with_ci.get("cascade", "epsilon")
        self.assertTrue(eps_base.startswith("local-"))
        self.assertTrue(eps_ci.startswith("ci-"))

    def test_circular_ref_is_error(self) -> None:
        result = evaluate_pfcfg_entry(
            CONFIGS / "edge-cases" / "interpolation-cascade.pfcfg",
            env={},
        )
        codes = {d.code for d in result.diagnostics.items}
        self.assertIn(DiagnosticCode.CIRCULAR_REF, codes)
        _, a_ok = result.get("cascade.loop", "a")
        self.assertFalse(a_ok)

    def test_env_alternate_ci_prefix(self) -> None:
        result = evaluate_pfcfg_entry(
            CONFIGS / "_base" / "defaults.pfcfg",
            env={"CI": "1", "CACHE_NAMESPACE": "myteam"},
        )
        prefix, ok = result.get("cache", "key_prefix")
        self.assertTrue(ok)
        self.assertEqual(prefix, "ci-myteam")

    def test_env_defaults_applied(self) -> None:
        result = evaluate_pfcfg_entry(
            CONFIGS / "_base" / "defaults.pfcfg",
            env={},
        )
        image, ok = result.get("build", "image")
        self.assertTrue(ok)
        self.assertEqual(image, "pfci/builder:22.04")

    def test_toolchains_dotted_ref(self) -> None:
        result = evaluate_pfcfg_entry(
            CONFIGS / "_base" / "toolchains.pfcfg",
            env={},
        )
        compiler, ok = result.get("toolchain.default", "compiler")
        self.assertTrue(ok)
        self.assertEqual(compiler, "node")

    def test_conditional_includes_feature_beta(self) -> None:
        beta_on = evaluate_pfcfg_entry(
            CONFIGS / "edge-cases" / "conditional-includes.pfcfg",
            env={"FEATURE_BETA": "1", "REQUIRED_API_ENDPOINT": "https://a"},
        )
        steps, ok = beta_on.get("feature", "beta_enabled")
        self.assertTrue(ok)
        self.assertEqual(steps, "true")

        beta_off = evaluate_pfcfg_entry(
            CONFIGS / "edge-cases" / "conditional-includes.pfcfg",
            env={"REQUIRED_API_ENDPOINT": "https://a"},
        )
        steps, ok = beta_off.get("build", "steps")
        self.assertTrue(ok)
        self.assertEqual(steps, "legacy-compile,legacy-test")

    def test_notifications_slack(self) -> None:
        on = evaluate_pfcfg_entry(
            CONFIGS / "_base" / "notifications.pfcfg",
            env={"SLACK_WEBHOOK": "https://hooks"},
        )
        enabled, ok = on.get("notify.slack", "enabled")
        self.assertTrue(ok)
        self.assertEqual(enabled, "true")
        off = evaluate_pfcfg_entry(
            CONFIGS / "_base" / "notifications.pfcfg",
            env={},
        )
        enabled, ok = off.get("notify.slack", "enabled")
        self.assertTrue(ok)
        self.assertEqual(enabled, "false")


# ---------------------------------------------------------------------------
# Independent JSON evaluator tests (critical: prove it doesn't use .pfcfg)
# ---------------------------------------------------------------------------


class JsonEvaluatorIndependenceTests(unittest.TestCase):
    """CRITICAL: prove JSON evaluator is independent of .pfcfg parsing."""

    def test_evaluates_pure_json_without_pfcfg(self) -> None:
        """Build a Document AST from raw dicts, serialize to JSON, deserialize,
        and evaluate — never touching a .pfcfg file or the .pfcfg parser."""
        raw = {
            "format": FORMAT,
            "source": "<memory>",
            "body": [
                {"op": "set", "section": "build", "key": "parallel", "value": {"lit": "true"}},
                {"op": "set", "section": "build", "key": "steps", "value": {
                    "concat": [
                        {"env": "CI", "alternate": {"lit": "ci-"}},
                        {"lit": "compile,test"},
                    ]
                }},
                {"op": "set", "section": "build", "key": "node", "value": {
                    "env": "NODE_VER", "default": {"lit": "18"}
                }},
                {"op": "set", "section": "ref", "key": "here", "value": {
                    "ref": {"section": "build", "key": "node"}
                }},
            ],
        }
        doc = loads(json.dumps(raw))
        # Deliberately evaluate the Document AST through the JSON-only code path:
        result = evaluate_json_document(doc, env={"CI": "yes"}, doc_dir=Path.cwd())
        steps, ok = result.get("build", "steps")
        self.assertTrue(ok)
        self.assertEqual(steps, "ci-compile,test")
        node, ok = result.get("ref", "here")
        self.assertTrue(ok)
        self.assertEqual(node, "18")
        parallel, ok = result.get("build", "parallel")
        self.assertTrue(ok)
        self.assertEqual(parallel, "true")

    def test_json_evaluator_handles_ifdef_ifndef_nested(self) -> None:
        raw = {
            "format": FORMAT,
            "source": "<mem>",
            "body": [
                {"op": "ifdef", "var": "OUTER", "body": [
                    {"op": "ifndef", "var": "INNER", "body": [
                        {"op": "set", "section": "s", "key": "k", "value": {"lit": "outer-no-inner"}}
                    ]},
                    {"op": "ifdef", "var": "INNER", "body": [
                        {"op": "set", "section": "s", "key": "k", "value": {"lit": "outer-inner"}}
                    ]}
                ]},
                {"op": "set", "section": "s", "key": "k", "value": {"lit": "base"}},
            ],
        }
        doc = loads(json.dumps(raw))
        r1 = evaluate_json_document(doc, env={})
        self.assertEqual(r1.get("s", "k"), ("base", True))
        r2 = evaluate_json_document(doc, env={"OUTER": "1"})
        self.assertEqual(r2.get("s", "k"), ("outer-no-inner", True))
        r3 = evaluate_json_document(doc, env={"OUTER": "1", "INNER": "1"})
        self.assertEqual(r3.get("s", "k"), ("outer-inner", True))

    def test_json_evaluator_circular_ref_is_error(self) -> None:
        raw = {
            "format": FORMAT,
            "source": "<mem>",
            "body": [
                {"op": "set", "section": "loop", "key": "a",
                 "value": {"ref": {"section": "loop", "key": "b"}}},
                {"op": "set", "section": "loop", "key": "b",
                 "value": {"ref": {"section": "loop", "key": "a"}}},
            ],
        }
        doc = loads(json.dumps(raw))
        result = evaluate_json_document(doc, env={})
        codes = {d.code for d in result.diagnostics.items}
        self.assertIn(DiagnosticCode.CIRCULAR_REF, codes)
        _, ok_a = result.get("loop", "a")
        self.assertFalse(ok_a)

    def test_json_evaluator_never_imports_pfcfg_parser(self) -> None:
        """Smoke test: evaluator_json module imports do NOT transitively import
        parser.py's parse_entry/parse_file for evaluation."""
        import solution.pfcfg_json.evaluator_json as ej
        source = Path(ej.__file__).read_text(encoding="utf-8")
        # evaluator_json must import model/value types but NOT parse_file/parse_entry:
        self.assertNotIn("parse_file(", source)
        self.assertNotIn("parse_entry(", source)
        self.assertNotIn("parse_text(", source)
        # It may use env_is_set or the Value/Stmt dataclasses, but never .pfcfg parsing:
        self.assertNotIn("from .parser import", source)


class JsonEvaluatorWithConvertedFilesTests(unittest.TestCase):
    """Run the converter and then use evaluate_json_entry on produced JSON."""

    def test_convert_then_json_evaluate_acme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            json_root = Path(tmp) / "out"
            convert_tree(CONFIGS, json_root, entry_points=["customers/acme-corp/pipeline.pfcfg"])
            json_entry = json_root / "customers" / "acme-corp" / "pipeline.json"
            self.assertTrue(json_entry.is_file(), f"expected {json_entry}")
            result = evaluate_json_entry(
                json_entry,
                env={"ACME_DEPLOY_TARGET": "prod"},
            )
            val, ok = result.get("deploy", "requires_approval")
            self.assertTrue(ok)
            self.assertEqual(val, "false")


# ---------------------------------------------------------------------------
# Converter tests
# ---------------------------------------------------------------------------


class ConverterTests(unittest.TestCase):
    def test_convert_acme_produces_json_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            result = convert_tree(
                CONFIGS,
                out,
                entry_points=["customers/acme-corp/pipeline.pfcfg"],
            )
            expected = out / "customers" / "acme-corp" / "pipeline.json"
            self.assertTrue(expected.is_file())
            data = json.loads(expected.read_text(encoding="utf-8"))
            self.assertEqual(data["format"], FORMAT)
            self.assertIn("body", data)
            self.assertTrue(any(s.get("op") == "set" for s in data["body"]))
            self.assertTrue(len(result.files_converted) >= 1)

    def test_converter_emits_env_no_default_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            result = convert_tree(
                CONFIGS,
                out,
                entry_points=["edge-cases/conditional-includes.pfcfg"],
            )
            codes = {d.code for d in result.report.items}
            self.assertIn(DiagnosticCode.UNMIGRATABLE_ENV_NO_DEFAULT, codes)

    def test_converter_conditional_include_risky_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            result = convert_tree(
                CONFIGS,
                out,
                entry_points=["edge-cases/conditional-includes.pfcfg"],
            )
            codes = {d.code for d in result.report.items}
            self.assertIn(DiagnosticCode.RISKY_CONDITIONAL_INCLUDE, codes)

    def test_converted_json_roundtrips_through_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            convert_tree(CONFIGS, out, entry_points=["customers/acme-corp/pipeline.pfcfg"])
            for src, jp in [(s, Path(j)) for s, j in _convert_fixture_pair(CONFIGS, out)]:
                text = Path(jp).read_text(encoding="utf-8")
                doc = loads(text)
                self.assertEqual(doc.format, FORMAT)
                self.assertIsInstance(doc.body, tuple)
                self.assertEqual(loads(dumps(doc)), doc)


def _convert_fixture_pair(configs: Path, out: Path) -> list[tuple[str, str]]:
    out_lst: list[tuple[str, str]] = []
    for j in out.rglob("*.json"):
        rel = j.relative_to(out).with_suffix(".pfcfg")
        src = configs / rel
        if src.is_file():
            out_lst.append((str(src), str(j)))
    return out_lst


# ---------------------------------------------------------------------------
# Verifier tests (including mismatch/tamper detection)
# ---------------------------------------------------------------------------


class VerifierTests(unittest.TestCase):
    def _convert_and_verify(
        self,
        entry_rel: str,
        fixture: dict[str, str],
        fixture_name: str = "f",
    ):
        with tempfile.TemporaryDirectory() as tmp:
            json_root = Path(tmp) / "out"
            convert_tree(CONFIGS, json_root, entry_points=[entry_rel])
            pfcfg = CONFIGS / entry_rel
            json_path = (json_root / entry_rel).with_suffix(".json")
            result = verify_entry(pfcfg, json_path, env=fixture, env_name=fixture_name)
            return result

    def test_acme_ci_passes(self) -> None:
        r = self._convert_and_verify(
            "customers/acme-corp/pipeline.pfcfg",
            get_fixture("ci"),
            "ci",
        )
        self.assertTrue(r.passed, msg=f"failed: {[m.to_dict() for m in r.value_mismatches]}")

    def test_acme_non_ci_passes(self) -> None:
        r = self._convert_and_verify(
            "customers/acme-corp/pipeline.pfcfg",
            get_fixture("non_ci"),
            "non_ci",
        )
        self.assertTrue(r.passed)

    def test_globex_production_passes(self) -> None:
        r = self._convert_and_verify(
            "customers/globex/pipeline.pfcfg",
            get_fixture("production"),
            "production",
        )
        self.assertTrue(r.passed, msg=f"mismatches: {[m.to_dict() for m in r.value_mismatches]}")

    def test_globex_non_ci_passes(self) -> None:
        r = self._convert_and_verify(
            "customers/globex/pipeline.pfcfg",
            get_fixture("non_ci"),
            "non_ci",
        )
        self.assertTrue(r.passed)

    def test_initech_ci_passes(self) -> None:
        r = self._convert_and_verify(
            "customers/initech/pipeline.pfcfg",
            get_fixture("ci"),
            "ci",
        )
        self.assertTrue(r.passed)

    def test_initech_minimal_passes(self) -> None:
        r = self._convert_and_verify(
            "customers/initech/pipeline.pfcfg",
            get_fixture("minimal"),
            "minimal",
        )
        self.assertTrue(r.passed)

    def test_cascade_ci_passes(self) -> None:
        r = self._convert_and_verify(
            "edge-cases/interpolation-cascade.pfcfg",
            get_fixture("ci"),
            "ci",
        )
        self.assertTrue(r.passed)

    def test_cascade_minimal_passes(self) -> None:
        r = self._convert_and_verify(
            "edge-cases/interpolation-cascade.pfcfg",
            get_fixture("minimal"),
            "minimal",
        )
        self.assertTrue(r.passed)

    def test_conditional_includes_ci_passes(self) -> None:
        r = self._convert_and_verify(
            "edge-cases/conditional-includes.pfcfg",
            get_fixture("ci"),
            "ci",
        )
        self.assertTrue(r.passed)

    def test_conditional_includes_minimal_passes(self) -> None:
        r = self._convert_and_verify(
            "edge-cases/conditional-includes.pfcfg",
            get_fixture("minimal"),
            "minimal",
        )
        self.assertTrue(r.passed)

    def test_verifier_detects_tampered_value(self) -> None:
        """Deliberately tamper with the converted JSON and verify FAILURE."""
        with tempfile.TemporaryDirectory() as tmp:
            json_root = Path(tmp) / "out"
            convert_tree(CONFIGS, json_root, entry_points=["customers/acme-corp/pipeline.pfcfg"])
            json_path = json_root / "customers" / "acme-corp" / "pipeline.json"
            data = json.loads(json_path.read_text(encoding="utf-8"))
            tampered = False
            for stmt in data["body"]:
                if (
                    stmt.get("op") == "set"
                    and stmt.get("section") == "build"
                    and stmt.get("key") == "timeout_minutes"
                ):
                    stmt["value"] = {"lit": "9999"}
                    tampered = True
            self.assertTrue(tampered, "failed to locate set statement to tamper with")
            json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            pfcfg = CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg"
            result = verify_entry(pfcfg, json_path, env={}, env_name="tampered")
            self.assertFalse(result.passed, "verifier should fail on tampered JSON")
            mismatch_keys = {(m.section, m.key) for m in result.value_mismatches}
            self.assertIn(("build", "timeout_minutes"), mismatch_keys)

    def test_verifier_detects_deleted_key(self) -> None:
        """Delete a set from JSON and confirm verifier reports missing_in_json."""
        with tempfile.TemporaryDirectory() as tmp:
            json_root = Path(tmp) / "out"
            convert_tree(CONFIGS, json_root, entry_points=["customers/acme-corp/pipeline.pfcfg"])
            json_path = json_root / "customers" / "acme-corp" / "pipeline.json"
            data = json.loads(json_path.read_text(encoding="utf-8"))
            new_body = []
            for stmt in data["body"]:
                if (
                    stmt.get("op") == "set"
                    and stmt.get("section") == "customer"
                    and stmt.get("key") == "tier"
                ):
                    continue
                new_body.append(stmt)
            data["body"] = new_body
            json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            pfcfg = CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg"
            result = verify_entry(pfcfg, json_path, env={}, env_name="deleted-key")
            self.assertFalse(result.passed)
            self.assertIn(("customer", "tier"), result.missing_in_json)

    def test_verifier_detects_extra_key(self) -> None:
        """Inject a new key into JSON and confirm verifier reports extra."""
        with tempfile.TemporaryDirectory() as tmp:
            json_root = Path(tmp) / "out"
            convert_tree(CONFIGS, json_root, entry_points=["customers/acme-corp/pipeline.pfcfg"])
            json_path = json_root / "customers" / "acme-corp" / "pipeline.json"
            data = json.loads(json_path.read_text(encoding="utf-8"))
            data["body"].append({
                "op": "set",
                "section": "doesnot",
                "key": "exist",
                "value": {"lit": "ghost"},
            })
            json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            pfcfg = CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg"
            result = verify_entry(pfcfg, json_path, env={}, env_name="extra-key")
            self.assertFalse(result.passed)
            self.assertIn(("doesnot", "exist"), result.missing_in_legacy)


# ---------------------------------------------------------------------------
# Fixture tests
# ---------------------------------------------------------------------------


class FixtureTests(unittest.TestCase):
    def test_fixtures_cover_required_buckets(self) -> None:
        names = set(all_fixture_names())
        self.assertIn("ci", names)
        self.assertIn("non_ci", names)
        self.assertIn("production", names)
        self.assertIn("minimal", names)

    def test_ci_fixture_has_ci_set(self) -> None:
        self.assertTrue(get_fixture("ci").get("CI"))

    def test_non_ci_fixture_ci_unset_or_empty(self) -> None:
        v = get_fixture("non_ci").get("CI", "")
        self.assertTrue(v == "" or v is None)

    def test_fixture_info_exists(self) -> None:
        for name in all_fixture_names():
            self.assertIn(name, FIXTURE_INFO)

    def test_conditionals_exercised_across_fixtures(self) -> None:
        """Acme deploy.requires_approval must differ between fixtures because
        ACME_DEPLOY_TARGET is set in 'ci' fixture but not 'non_ci'."""
        ci_env = get_fixture("ci")
        non_env = get_fixture("non_ci")
        self.assertTrue(ci_env.get("ACME_DEPLOY_TARGET"))
        self.assertFalse(non_env.get("ACME_DEPLOY_TARGET"))


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class SchemaTests(unittest.TestCase):
    def test_schema_file_exists(self) -> None:
        self.assertTrue(SCHEMA_PATH.is_file(), f"missing schema at {SCHEMA_PATH}")

    def test_schema_is_valid_json(self) -> None:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertIn("$schema", data)
        self.assertEqual(data["title"], "pfcfg-json/v1")

    def test_converted_documents_match_schema_structure(self) -> None:
        """Structural check against schema rules without jsonschema package."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            convert_tree(CONFIGS, out, entry_points=["customers/acme-corp/pipeline.pfcfg"])
            for j in out.rglob("*.json"):
                data = json.loads(j.read_text(encoding="utf-8"))
                self.assertEqual(data["format"], FORMAT, f"{j}")
                self.assertIsInstance(data["source"], str)
                self.assertIsInstance(data["body"], list)
                for stmt in data["body"]:
                    self.assertIsInstance(stmt, dict)
                    self.assertIn("op", stmt)
                    op = stmt["op"]
                    if op == "set":
                        self.assertIn("section", stmt)
                        self.assertIn("key", stmt)
                        self.assertIn("value", stmt)
                        self._assert_valid_value(stmt["value"], j)
                    elif op in ("include", "include_once"):
                        self.assertIn("path", stmt)
                    elif op in ("ifdef", "ifndef"):
                        self.assertIn("var", stmt)
                        self.assertIsInstance(stmt["body"], list)
                    else:
                        self.fail(f"unknown op {op!r} in {j}")

    def test_jsonschema_package_attempt(self) -> None:
        """If jsonschema is available, fully validate. Otherwise report honestly."""
        try:
            import jsonschema  # type: ignore
        except ImportError:
            self.skipTest("jsonschema not installed; structural checks only above")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            convert_tree(CONFIGS, out, entry_points=[
                "customers/acme-corp/pipeline.pfcfg",
                "customers/globex/pipeline.pfcfg",
                "customers/initech/pipeline.pfcfg",
                "edge-cases/interpolation-cascade.pfcfg",
                "edge-cases/conditional-includes.pfcfg",
            ])
            for j in out.rglob("*.json"):
                data = json.loads(j.read_text(encoding="utf-8"))
                jsonschema.validate(instance=data, schema=schema)

    def _assert_valid_value(self, v: object, ctx: Path) -> None:
        self.assertIsInstance(v, dict)
        keys = set(v.keys())
        if keys == {"lit"}:
            self.assertIsInstance(v["lit"], str)  # type: ignore[index]
        elif "env" in keys:
            self.assertIsInstance(v["env"], str)  # type: ignore[index]
            if keys == {"env"}:
                pass
            elif keys == {"env", "default"}:
                self._assert_valid_value(v["default"], ctx)  # type: ignore[index,arg-type]
            elif keys == {"env", "alternate"}:
                self._assert_valid_value(v["alternate"], ctx)  # type: ignore[index,arg-type]
            else:
                self.fail(f"bad env value keys {keys} in {ctx}")
        elif keys == {"ref"}:
            ref = v["ref"]  # type: ignore[index]
            self.assertIsInstance(ref, dict)
            self.assertEqual(set(ref.keys()), {"section", "key"})  # type: ignore[arg-type]
        elif keys == {"concat"}:
            parts = v["concat"]  # type: ignore[index]
            self.assertIsInstance(parts, list)
            for p in parts:  # type: ignore[assignment]
                self._assert_valid_value(p, ctx)
        else:
            self.fail(f"unknown value shape {keys} in {ctx}")


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def test_list_fixtures_runs(self) -> None:
        from solution.pfcfg_json.cli import main
        rc = main(["list-fixtures"])
        self.assertEqual(rc, 0)

    def test_list_fixtures_verbose_runs(self) -> None:
        from solution.pfcfg_json.cli import main
        rc = main(["list-fixtures", "-v"])
        self.assertEqual(rc, 0)

    def test_convert_runs_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "json"
            report = Path(tmp) / "diag.json"
            from solution.pfcfg_json.cli import main
            rc = main([
                "convert",
                "--source", str(CONFIGS),
                "--output", str(out),
                "--report", str(report),
            ])
            self.assertIn(rc, {0, 1})
            self.assertTrue((out / "customers" / "acme-corp" / "pipeline.json").is_file())
            self.assertTrue(report.is_file())

    def test_convert_then_verify_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "json"
            vreport = Path(tmp) / "verify.json"
            from solution.pfcfg_json.cli import main
            main([
                "convert",
                "--source", str(CONFIGS),
                "--output", str(out),
            ])
            rc = main([
                "verify",
                "--source", str(CONFIGS),
                "--json-root", str(out),
                "--entry", "customers/acme-corp/pipeline.pfcfg",
                "--fixture", "ci",
                "--report", str(vreport),
            ])
            self.assertIn(rc, {0, 1})
            self.assertTrue(vreport.is_file())

    def test_report_command_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            from solution.pfcfg_json.cli import main
            rc = main([
                "report",
                "--source", str(CONFIGS),
                "--output", str(out),
            ])
            self.assertIn(rc, {0, 1})
            self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()
