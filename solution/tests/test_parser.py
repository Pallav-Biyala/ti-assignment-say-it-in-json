"""Parser tests against starter configs and small fixtures."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from solution.pfcfg_json import (
    Concat,
    Env,
    EnvAlternate,
    EnvDefault,
    Ifdef,
    Ifndef,
    Include,
    IncludeOnce,
    Literal,
    ParseError,
    Ref,
    Set,
    dumps,
    loads,
    parse_entry,
    parse_file,
    parse_text,
    parse_value,
)

REPO = Path(__file__).resolve().parents[2]
CONFIGS = REPO / "starter" / "configs"


class InterpolationTests(unittest.TestCase):
    def test_nested_default_and_ref_and_concat(self) -> None:
        value = parse_value(
            "${ACME_RELEASE_TAG:-$(build.node_version)-${GIT_SHA:-dev}}"
        )
        self.assertEqual(
            value,
            EnvDefault(
                "ACME_RELEASE_TAG",
                Concat(
                    (
                        Ref("build", "node_version"),
                        Literal("-"),
                        EnvDefault("GIT_SHA", Literal("dev")),
                    )
                ),
            ),
        )

    def test_alternate_concat(self) -> None:
        value = parse_value("${CI:+ci-}${CACHE_NAMESPACE:-default}")
        self.assertEqual(
            value,
            Concat(
                (
                    EnvAlternate("CI", Literal("ci-")),
                    EnvDefault("CACHE_NAMESPACE", Literal("default")),
                )
            ),
        )

    def test_dotted_section_ref(self) -> None:
        self.assertEqual(
            parse_value("$(toolchain.default.compiler)"),
            Ref("toolchain.default", "compiler"),
        )

    def test_does_not_expand(self) -> None:
        self.assertEqual(parse_value("${REQUIRED_API_ENDPOINT}"), Env("REQUIRED_API_ENDPOINT"))


class StarterConfigTests(unittest.TestCase):
    def test_acme_pipeline_order_and_literals(self) -> None:
        doc = parse_file(CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg", env={})
        body = doc.body
        self.assertEqual(body[0], Include("../../templates/container-publish.pfcfg"))
        self.assertEqual(body[1], Include("staging.pfcfg"))
        sets = [s for s in body if isinstance(s, Set)]
        self.assertEqual(sets[0], Set("customer", "id", Literal("acme-corp")))
        timeout = next(s for s in sets if s.section == "build" and s.key == "timeout_minutes")
        self.assertEqual(timeout.value, Literal("90"))
        parallel = next(s for s in sets if s.section == "build" and s.key == "parallel")
        self.assertEqual(parallel.value, Literal("true"))
        tag = next(s for s in sets if s.section == "container" and s.key == "tag")
        self.assertIsInstance(tag.value, EnvDefault)
        ifdef = next(s for s in body if isinstance(s, Ifdef) and s.var == "ACME_DEPLOY_TARGET")
        self.assertEqual(ifdef.body, ())

    def test_acme_ifdef_active(self) -> None:
        doc = parse_file(
            CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg",
            env={"ACME_DEPLOY_TARGET": "prod"},
        )
        ifdef = next(s for s in doc.body if isinstance(s, Ifdef) and s.var == "ACME_DEPLOY_TARGET")
        self.assertEqual(
            ifdef.body,
            (Set("deploy", "requires_approval", Literal("false")),),
        )

    def test_globex_conditional_includes_depend_on_env(self) -> None:
        empty = parse_file(CONFIGS / "customers" / "globex" / "pipeline.pfcfg", env={})
        self.assertEqual(empty.body[0], Include("../../_base/defaults.pfcfg"))
        self.assertEqual(empty.body[1], Include("../../environments/ci-shared.pfcfg"))
        ifdef = empty.body[2]
        ifndef = empty.body[3]
        assert isinstance(ifdef, Ifdef) and isinstance(ifndef, Ifndef)
        self.assertEqual(ifdef.var, "PRODUCTION")
        self.assertEqual(ifdef.body, ())
        self.assertEqual(ifndef.body, (Include("overrides.pfcfg"),))

        prod = parse_file(
            CONFIGS / "customers" / "globex" / "pipeline.pfcfg",
            env={"PRODUCTION": "1"},
        )
        ifdef_p = prod.body[2]
        ifndef_p = prod.body[3]
        assert isinstance(ifdef_p, Ifdef) and isinstance(ifndef_p, Ifndef)
        self.assertEqual(
            ifdef_p.body,
            (Include("../../environments/on-prem.pfcfg"),),
        )
        self.assertEqual(ifndef_p.body, ())

    def test_initech_cross_key_concat(self) -> None:
        doc = parse_file(CONFIGS / "customers" / "initech" / "pipeline.pfcfg", env={})
        compiler = next(
            s
            for s in doc.body
            if isinstance(s, Set) and s.section == "build" and s.key == "compiler_path"
        )
        self.assertEqual(
            compiler.value,
            Concat(
                (
                    Literal("/usr/local/bin/"),
                    Ref("toolchain.default", "compiler"),
                )
            ),
        )

    def test_conditional_includes_feature_beta(self) -> None:
        off = parse_file(CONFIGS / "edge-cases" / "conditional-includes.pfcfg", env={})
        self.assertEqual(off.body[0], Ifdef("FEATURE_BETA", ()))
        ifndef = off.body[1]
        assert isinstance(ifndef, Ifndef)
        self.assertEqual(ifndef.body[0], Include("../_base/defaults.pfcfg"))
        self.assertEqual(
            ifndef.body[1],
            Set("build", "steps", Literal("legacy-compile,legacy-test")),
        )
        endpoint = next(
            s
            for s in off.body
            if isinstance(s, Set) and s.section == "migration" and s.key == "api_endpoint"
        )
        self.assertEqual(endpoint.value, Env("REQUIRED_API_ENDPOINT"))

        on = parse_file(
            CONFIGS / "edge-cases" / "conditional-includes.pfcfg",
            env={"FEATURE_BETA": "1"},
        )
        ifdef = on.body[0]
        assert isinstance(ifdef, Ifdef)
        self.assertEqual(ifdef.body, (Include("../templates/node-build.pfcfg"),))
        self.assertEqual(on.body[1], Ifndef("FEATURE_BETA", ()))

    def test_defaults_include_once_before_sections(self) -> None:
        doc = parse_file(CONFIGS / "_base" / "defaults.pfcfg", env={})
        self.assertEqual(doc.body[0], IncludeOnce("toolchains.pfcfg"))
        self.assertEqual(doc.body[1], IncludeOnce("notifications.pfcfg"))
        self.assertIsInstance(doc.body[2], Set)

    def test_toolchains_dotted_section(self) -> None:
        doc = parse_file(CONFIGS / "_base" / "toolchains.pfcfg", env={})
        default = next(
            s
            for s in doc.body
            if isinstance(s, Set) and s.section == "toolchain.default" and s.key == "compiler"
        )
        self.assertEqual(default.value, Ref("toolchain.node", "binary"))

    def test_notifications_slack_env(self) -> None:
        off = parse_file(CONFIGS / "_base" / "notifications.pfcfg", env={})
        ifdef = next(s for s in off.body if isinstance(s, Ifdef) and s.var == "SLACK_WEBHOOK")
        ifndef = next(s for s in off.body if isinstance(s, Ifndef) and s.var == "SLACK_WEBHOOK")
        self.assertEqual(ifdef.body, ())
        self.assertEqual(
            ifndef.body,
            (Set("notify.slack", "enabled", Literal("false")),),
        )

        on = parse_file(
            CONFIGS / "_base" / "notifications.pfcfg",
            env={"SLACK_WEBHOOK": "https://hooks.example.invalid"},
        )
        ifdef_on = next(s for s in on.body if isinstance(s, Ifdef) and s.var == "SLACK_WEBHOOK")
        self.assertEqual(ifdef_on.body[0], Set("notify.slack", "enabled", Literal("true")))
        self.assertEqual(next(s for s in on.body if isinstance(s, Ifndef)).body, ())

    def test_cascade_keeps_circular_refs_unevaluated(self) -> None:
        doc = parse_file(CONFIGS / "edge-cases" / "interpolation-cascade.pfcfg", env={})
        a = next(s for s in doc.body if isinstance(s, Set) and s.key == "a")
        b = next(s for s in doc.body if isinstance(s, Set) and s.key == "b")
        self.assertEqual(a.section, "cascade.loop")
        self.assertEqual(a.value, Ref("cascade.loop", "b"))
        self.assertEqual(b.value, Ref("cascade.loop", "a"))

    def test_json_roundtrip_acme(self) -> None:
        doc = parse_file(CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg", env={})
        self.assertEqual(loads(dumps(doc)), doc)

    def test_parse_entry_acme_closure(self) -> None:
        docs = parse_entry(CONFIGS / "customers" / "acme-corp" / "pipeline.pfcfg", env={})
        names = {Path(p).name for p in docs}
        self.assertEqual(
            names,
            {
                "pipeline.pfcfg",
                "container-publish.pfcfg",
                "node-build.pfcfg",
                "defaults.pfcfg",
                "toolchains.pfcfg",
                "notifications.pfcfg",
                "staging.pfcfg",
            },
        )

    def test_parse_entry_globex_switches_overlays(self) -> None:
        empty = parse_entry(CONFIGS / "customers" / "globex" / "pipeline.pfcfg", env={})
        self.assertTrue(any(p.endswith("overrides.pfcfg") for p in empty))
        self.assertFalse(any(p.endswith("on-prem.pfcfg") for p in empty))

        prod = parse_entry(
            CONFIGS / "customers" / "globex" / "pipeline.pfcfg",
            env={"PRODUCTION": "1"},
        )
        self.assertTrue(any(p.endswith("on-prem.pfcfg") for p in prod))
        self.assertFalse(any(p.endswith("overrides.pfcfg") for p in prod))

    def test_empty_ci_is_unset(self) -> None:
        doc = parse_file(
            CONFIGS / "templates" / "container-publish.pfcfg",
            env={"CI": ""},
        )
        ifdef = next(s for s in doc.body if isinstance(s, Ifdef) and s.var == "CI")
        self.assertEqual(ifdef.body, ())


class ErrorTests(unittest.TestCase):
    def test_unknown_directive(self) -> None:
        with self.assertRaises(ParseError) as ctx:
            parse_text("@unknown foo\n", source="t.pfcfg", env={})
        self.assertIn("unknown directive", str(ctx.exception))

    def test_include_after_section(self) -> None:
        text = "[build]\nparallel = true\n@include other.pfcfg\n"
        with self.assertRaises(ParseError) as ctx:
            parse_text(text, source="t.pfcfg", env={})
        self.assertIn("before any section headers", str(ctx.exception))

    def test_unmatched_endif(self) -> None:
        with self.assertRaises(ParseError):
            parse_text("@endif\n", source="t.pfcfg", env={})

    def test_missing_endif(self) -> None:
        with self.assertRaises(ParseError) as ctx:
            parse_text("@ifdef CI\n[build]\nx = 1\n", source="t.pfcfg", env={"CI": "1"})
        self.assertIn("missing @endif", str(ctx.exception))

    def test_include_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.pfcfg"
            b = root / "b.pfcfg"
            a.write_text("@include b.pfcfg\n", encoding="utf-8")
            b.write_text("@include a.pfcfg\n", encoding="utf-8")
            with self.assertRaises(ParseError) as ctx:
                parse_entry(a, env={})
            self.assertIn("include cycle", str(ctx.exception))

    def test_nested_ifdef(self) -> None:
        text = (
            "@ifdef OUTER\n"
            "@ifdef INNER\n"
            "[build]\n"
            "flag = true\n"
            "@endif\n"
            "@endif\n"
        )
        off = parse_text(text, source="t.pfcfg", env={})
        self.assertEqual(off.body, (Ifdef("OUTER", ()),))
        both = parse_text(text, source="t.pfcfg", env={"OUTER": "1", "INNER": "1"})
        outer = both.body[0]
        assert isinstance(outer, Ifdef)
        inner = outer.body[0]
        assert isinstance(inner, Ifdef)
        self.assertEqual(inner.body, (Set("build", "flag", Literal("true")),))

    def test_inactive_section_does_not_block_later_include(self) -> None:
        text = (
            "@ifdef SKIP\n"
            "[build]\n"
            "x = 1\n"
            "@endif\n"
            "@include later.pfcfg\n"
        )
        doc = parse_text(text, source="t.pfcfg", env={})
        self.assertEqual(doc.body[0], Ifdef("SKIP", ()))
        self.assertEqual(doc.body[1], Include("later.pfcfg"))


if __name__ == "__main__":
    unittest.main()
