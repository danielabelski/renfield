"""Typed domain contracts for the cross-MCP orchestrator (typed-contracts plan,
Phase 2). Platform-generic: Renfield owns the ``DomainEnvelope`` shape + the
registry; plugins (Reva) register a per-domain contract that produces / verifies
/ renders an envelope from a sub-agent's result.

The orchestrator merge uses the registry as the top of the degradation ladder:

    Tier 1  registered contract → produce → verify → render   (this module)
       │ (no contract, produce=None, or verify fails)
    Tier 2  prose juxtaposition of the sub-agent's own answer  (orchestrator.py)
       │ (kill-switch)
    Tier 3  LLM synthesizer                                    (orchestrator.py)

A contract NEVER raises into the merge — the orchestrator wraps produce/verify/
render in try/except and demotes to Tier 2 on any failure, so a buggy plugin
contract degrades gracefully instead of breaking the turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from loguru import logger

# Cross-repo contract version. A plugin registering a contract built against a
# different major version is refused (fail-closed to Tier 2) — there is no
# coordinated two-repo CI, so version safety is a runtime guard (plan finding #6).
DOMAIN_CONTRACT_VERSION = 1

# status values (supersedes 3-lite's bool `incomplete` — plan finding A4)
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"
_VALID_STATUS = frozenset({STATUS_COMPLETE, STATUS_PARTIAL, STATUS_EMPTY, STATUS_ERROR})


@dataclass
class DomainEnvelope:
    """The typed result of one domain's sub-agent, produced deterministically
    from its tool results (or, later, constrained-decoded reasoning)."""

    domain: str
    status: str = STATUS_COMPLETE
    items: list[Any] = field(default_factory=list)
    summary: str | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUS:
            raise ValueError(f"invalid DomainEnvelope status: {self.status!r}")


@runtime_checkable
class DomainContract(Protocol):
    """A plugin-provided per-domain contract. All three methods run inside the
    orchestrator's try/except demotion guard — raising demotes to Tier 2."""

    version: int

    def produce(self, sub_result: dict) -> DomainEnvelope | None:
        """Build the envelope from the sub-agent result (tool data). Return None
        to decline (→ Tier 2), e.g. when the expected tool result isn't present."""
        ...

    def verify(self, envelope: DomainEnvelope, sub_result: dict) -> bool:
        """Cross-check the envelope against ground-truth tool data. False → demote."""
        ...

    def render(self, envelope: DomainEnvelope, lang: str) -> str:
        """Render the envelope to a prose section body (no header — the merge
        adds the role-keyed header)."""
        ...


_REGISTRY: dict[str, DomainContract] = {}


def register_domain_contract(domain: str, contract: DomainContract) -> None:
    """Register a per-domain contract. Version-mismatched contracts are refused
    (fail-closed to Tier 2 + a warning) rather than risking a skewed render."""
    ver = getattr(contract, "version", None)
    if ver != DOMAIN_CONTRACT_VERSION:
        logger.warning(
            f"domain contract '{domain}' version {ver} != {DOMAIN_CONTRACT_VERSION} "
            "— refused (fails closed to Tier 2). Bump both repos in lockstep."
        )
        try:
            from utils.metrics import record_contract_version_mismatch
            record_contract_version_mismatch(domain)
        except Exception:  # noqa: BLE001
            pass
        return
    _REGISTRY[domain] = contract
    logger.info(f"domain contract registered: {domain} (v{ver})")


def get_domain_contract(domain: str) -> DomainContract | None:
    return _REGISTRY.get(domain)


def clear_domain_contracts() -> None:
    """Test hook — reset the registry."""
    _REGISTRY.clear()
