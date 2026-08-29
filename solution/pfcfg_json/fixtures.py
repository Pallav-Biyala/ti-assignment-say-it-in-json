"""Environment fixtures for equivalence verification.

Each fixture is a named (env_name -> env_dict) mapping covering at least:
- CI-like: CI set and non-empty
- non-CI: CI unset or empty
- production: PRODUCTION set, CI set
- minimal/bare: almost no variables set
"""

from __future__ import annotations

from dataclasses import dataclass


FIXTURES: dict[str, dict[str, str]] = {
    "ci": {
        "CI": "true",
        "GIT_SHA": "abc123def",
        "CACHE_NAMESPACE": "acme-ci",
        "NODE_VERSION": "20",
        "GO_VERSION": "1.22",
        "RUST_VERSION": "stable",
        "PKG_MGR": "npm",
        "CONTAINER_REGISTRY": "registry.example.invalid",
        "CONTAINER_REPO": "acme/app",
        "DOCKERFILE": "Dockerfile",
        "BUILD_PLATFORMS": "linux/amd64,linux/arm64",
        "DEPLOY_APPROVAL": "false",
        "PF_BUILD_IMAGE": "pfci/builder:22.04",
        "NOTIFY_SUCCESS": "slack",
        "NOTIFY_FAILURE": "slack,email,pager",
        "BUILD_NOTIFY_LIST": "ci@example.invalid",
        "SLACK_WEBHOOK": "https://hooks.example.invalid/ci-webhook",
        "SLACK_CHANNEL": "#ci-builds",
        "TEST_RUNNER": "jest",
        "COVERAGE_MIN": "85",
        "LINT_STRICT": "true",
        "NODE_ENV": "test",
        "ACME_DEPLOY_TARGET": "production",
        "ACME_RELEASE_TAG": "v1.2.3",
        "GLOBEX_ENV": "staging",
        "PRODUCTION": "",
        "SIGNING_KEY_ID": "initech-ci-01",
        "SIGNING_ALG": "ed25519",
        "RELEASE_VERSION": "2.3.0",
        "SIGNING_SECRET_ID": "arn:vault:secret",
        "REQUIRED_SIGNING_SECRET": "redacted-but-present",
        "VAULT_ADDR": "https://vault.initech.example.invalid",
        "VAULT_SECRET_PATH": "initech/ci-production",
        "CASCADE_ALPHA": "alpha-from-ci",
        "CASCADE_GAMMA": "gamma-from-ci",
        "CASCADE_DELTA": "",
        "FEATURE_BETA": "1",
        "REQUIRED_API_ENDPOINT": "https://api.beta.example.invalid/v1",
        "OPTIONAL_API_ENDPOINT": "https://api.optional.example.invalid",
        "MIGRATION_AUDIT": "1",
        "MIGRATION_AUDIT_USER": "jordan@example.invalid",
        "MIGRATION_AUDIT_TICKET": "MIG-456",
    },
    "non_ci": {
        "CI": "",
        "GIT_SHA": "",
        "CACHE_NAMESPACE": "",
        "NODE_VERSION": "",
        "GO_VERSION": "",
        "RUST_VERSION": "",
        "PKG_MGR": "",
        "CONTAINER_REGISTRY": "",
        "CONTAINER_REPO": "",
        "DOCKERFILE": "",
        "BUILD_PLATFORMS": "",
        "DEPLOY_APPROVAL": "",
        "PF_BUILD_IMAGE": "",
        "NOTIFY_SUCCESS": "",
        "NOTIFY_FAILURE": "",
        "BUILD_NOTIFY_LIST": "",
        "SLACK_WEBHOOK": "",
        "SLACK_CHANNEL": "",
        "TEST_RUNNER": "",
        "COVERAGE_MIN": "",
        "LINT_STRICT": "",
        "NODE_ENV": "",
        "ACME_DEPLOY_TARGET": "",
        "ACME_RELEASE_TAG": "",
        "GLOBEX_ENV": "",
        "PRODUCTION": "",
        "SIGNING_KEY_ID": "",
        "SIGNING_ALG": "",
        "RELEASE_VERSION": "",
        "SIGNING_SECRET_ID": "",
        "REQUIRED_SIGNING_SECRET": "",
        "VAULT_ADDR": "",
        "VAULT_SECRET_PATH": "",
        "CASCADE_ALPHA": "",
        "CASCADE_GAMMA": "",
        "CASCADE_DELTA": "",
        "FEATURE_BETA": "",
        "REQUIRED_API_ENDPOINT": "https://api.default.example.invalid",
        "OPTIONAL_API_ENDPOINT": "",
        "MIGRATION_AUDIT": "",
        "MIGRATION_AUDIT_USER": "",
        "MIGRATION_AUDIT_TICKET": "",
    },
    "production": {
        "CI": "true",
        "GIT_SHA": "prod-sha-999",
        "CACHE_NAMESPACE": "prod-cache",
        "NODE_VERSION": "18",
        "GO_VERSION": "1.21",
        "RUST_VERSION": "1.75",
        "PKG_MGR": "yarn",
        "CONTAINER_REGISTRY": "registry-prod.example.invalid",
        "CONTAINER_REPO": "acme/prod-app",
        "DOCKERFILE": "Dockerfile.prod",
        "BUILD_PLATFORMS": "linux/amd64",
        "DEPLOY_APPROVAL": "true",
        "PF_BUILD_IMAGE": "pfci/builder:22.04-hardened",
        "NOTIFY_SUCCESS": "email",
        "NOTIFY_FAILURE": "email,pager",
        "BUILD_NOTIFY_LIST": "sre@example.invalid",
        "SLACK_WEBHOOK": "https://hooks.example.invalid/prod-webhook",
        "SLACK_CHANNEL": "#prod-releases",
        "TEST_RUNNER": "vitest",
        "COVERAGE_MIN": "90",
        "LINT_STRICT": "true",
        "NODE_ENV": "production",
        "PRODUCTION": "1",
        "ACME_DEPLOY_TARGET": "prod",
        "ACME_RELEASE_TAG": "prod-v4.0.0",
        "GLOBEX_ENV": "production",
        "SIGNING_KEY_ID": "prod-signing-root",
        "SIGNING_ALG": "rsa4096",
        "RELEASE_VERSION": "4.0.0",
        "SIGNING_SECRET_ID": "arn:aws:kms:...",
        "REQUIRED_SIGNING_SECRET": "prod-redacted",
        "VAULT_ADDR": "https://vault-prod.initech.example.invalid",
        "VAULT_SECRET_PATH": "initech/prod/signing",
        "CASCADE_ALPHA": "prod-alpha",
        "CASCADE_GAMMA": "",
        "CASCADE_DELTA": "prod-delta",
        "FEATURE_BETA": "",
        "REQUIRED_API_ENDPOINT": "https://api.prod.example.invalid",
        "OPTIONAL_API_ENDPOINT": "https://op.prod.example.invalid",
        "MIGRATION_AUDIT": "1",
        "MIGRATION_AUDIT_USER": "release-manager@globex.example.invalid",
        "MIGRATION_AUDIT_TICKET": "PROD-REL-2024",
    },
    "minimal": {
        "CI": "",
        "REQUIRED_API_ENDPOINT": "https://api.minimal.example.invalid",
        "REQUIRED_SIGNING_SECRET": "minimal-secret",
    },
}


ENTRY_CONFIGS_RELATIVE: list[str] = [
    "customers/acme-corp/pipeline.pfcfg",
    "customers/globex/pipeline.pfcfg",
    "customers/initech/pipeline.pfcfg",
    "edge-cases/interpolation-cascade.pfcfg",
    "edge-cases/conditional-includes.pfcfg",
]


@dataclass
class FixtureInfo:
    name: str
    description: str
    env: dict[str, str]


FIXTURE_INFO: dict[str, str] = {
    "ci": "CI-like environment with most variables set. Exercises @ifdef CI branches, shared overlays, fully-resolved interpolation.",
    "non_ci": "Developer-workstation-like environment. CI unset or empty. Exercises @ifndef CI and most defaults.",
    "production": "CI + PRODUCTION set. Exercises Globex on-prem overlay, hardened build image, stricter settings.",
    "minimal": "Bare environment with only the strictly-required env-no-default vars set. Maximises fallbacks and defaults.",
}


def get_fixture(name: str) -> dict[str, str]:
    if name not in FIXTURES:
        raise KeyError(f"unknown fixture: {name!r}; available: {sorted(FIXTURES)}")
    return dict(FIXTURES[name])


def all_fixture_names() -> list[str]:
    return sorted(FIXTURES.keys())
