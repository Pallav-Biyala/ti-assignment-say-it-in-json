"""pfcfg-json/v1: PipelineForge .pfcfg → JSON migration toolkit.

Modules:
    model         - Data model and JSON serialization for pfcfg-json/v1 AST.
    parser        - .pfcfg → AST parser.
    diagnostics   - Diagnostic codes, severities, and reporting.
    evaluator_legacy - Reference evaluator for legacy .pfcfg files.
    evaluator_json  - Independent evaluator for pfcfg-json/v1 JSON.
    converter     - .pfcfg → pfcfg-json/v1 JSON tree converter.
    verifier      - Equivalence verifier (legacy vs. JSON evaluation).
    fixtures      - Environment fixtures: ci, non_ci, production, minimal.
    cli           - Command-line interface (convert/verify/report).
"""

from .diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticReport,
    Severity,
)
from .evaluator_json import (
    JsonEffectiveConfig,
    JsonEvalError,
    evaluate_json_document,
    evaluate_json_entry,
)
from .evaluator_legacy import (
    EffectiveConfig,
    EvalError,
    evaluate_pfcfg_entry,
)
from .fixtures import (
    ENTRY_CONFIGS_RELATIVE,
    FIXTURE_INFO,
    FIXTURES,
    all_fixture_names,
    get_fixture,
)
from .model import (
    FORMAT,
    Assignment,
    Concat,
    Document,
    Env,
    EnvAlternate,
    EnvDefault,
    Ifdef,
    Ifndef,
    Include,
    IncludeOnce,
    Literal,
    ModelError,
    Ref,
    Set,
    Stmt,
    Value,
    document_from_json,
    document_to_json,
    dump,
    dumps,
    load,
    loads,
)
from .parser import (
    ParseError,
    env_is_set,
    parse_entry,
    parse_file,
    parse_text,
    parse_value,
)
from .converter import (
    ConversionResult,
    convert_single,
    convert_tree,
)
from .verifier import (
    KeyMismatch,
    VerifyResult,
    verify_entry,
)

__all__ = [
    "FORMAT",
    "Assignment",
    "Concat",
    "Document",
    "Env",
    "EnvAlternate",
    "EnvDefault",
    "Ifdef",
    "Ifndef",
    "Include",
    "IncludeOnce",
    "Literal",
    "ModelError",
    "Ref",
    "Set",
    "Stmt",
    "Value",
    "document_from_json",
    "document_to_json",
    "dump",
    "dumps",
    "load",
    "loads",
    "ParseError",
    "env_is_set",
    "parse_entry",
    "parse_file",
    "parse_text",
    "parse_value",
    "Diagnostic",
    "DiagnosticCode",
    "DiagnosticReport",
    "Severity",
    "EffectiveConfig",
    "EvalError",
    "evaluate_pfcfg_entry",
    "JsonEffectiveConfig",
    "JsonEvalError",
    "evaluate_json_document",
    "evaluate_json_entry",
    "ConversionResult",
    "convert_single",
    "convert_tree",
    "KeyMismatch",
    "VerifyResult",
    "verify_entry",
    "FIXTURES",
    "FIXTURE_INFO",
    "ENTRY_CONFIGS_RELATIVE",
    "all_fixture_names",
    "get_fixture",
]
