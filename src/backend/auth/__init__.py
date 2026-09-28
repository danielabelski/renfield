"""Renfield pluggable authentication: provider contract + registry.

Public surface:
    ProviderResult, AuthProvider, PROVIDER_RESULT_CONTRACT_VERSION
        — the cross-repo contract (ebongard/renfield#591).
    ProviderRegistry, get_registry, build_default_registry
        — priority-ordered, multi-active provider registry.
    resolve_login
        — runs the credential walk, fires ``post_authenticate`` once, and
          returns the resolution outcome for ``/auth/login``.
"""

from auth.login_flow import LoginOutcome, resolve_login
from auth.provider_contract import (
    PROVIDER_RESULT_CONTRACT_VERSION,
    AuthProvider,
    ProviderResult,
)
from auth.registry import (
    ProviderRegistry,
    build_default_registry,
    get_registry,
    set_registry,
)

__all__ = [
    "PROVIDER_RESULT_CONTRACT_VERSION",
    "AuthProvider",
    "LoginOutcome",
    "ProviderRegistry",
    "ProviderResult",
    "build_default_registry",
    "get_registry",
    "resolve_login",
    "set_registry",
]
