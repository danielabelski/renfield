"""Tests for the `agent_system_prompt` hook seam (services.agent_service).

Lets a plugin (e.g. an edition's role-specific prompts) prepend a role system
prompt to the agent's JSON system message. Contract:
  - 0 handlers  → byte-identical (standalone Renfield unchanged)
  - 1st non-empty string handler result → prepended ("<role>\\n\\n<base>")
  - None / blank / raising handlers → ignored (fail-open)
"""

import asyncio

import pytest

from services.agent_service import _apply_agent_system_prompt_hook
from utils.hooks import clear_hooks, register_hook

_BASE = "Antworte nur mit JSON."


@pytest.fixture(autouse=True)
def _isolate_hooks():
    clear_hooks()
    yield
    clear_hooks()


def _run(coro):
    return asyncio.run(coro)


def test_no_handler_is_byte_identical():
    out = _run(_apply_agent_system_prompt_hook(_BASE, role="release", lang="de"))
    assert out == _BASE


def test_handler_prepends_and_preserves_json_directive():
    async def handler(role=None, lang=None, **kw):
        return f"ROLE-SYS::{role}::{lang}"

    register_hook("agent_system_prompt", handler)
    out = _run(_apply_agent_system_prompt_hook(_BASE, role="advisor", lang="en"))
    assert out == "ROLE-SYS::advisor::en\n\n" + _BASE
    assert out.endswith(_BASE)  # the JSON format directive survives


def test_none_result_keeps_base():
    async def handler(**kw):
        return None

    register_hook("agent_system_prompt", handler)
    assert _run(_apply_agent_system_prompt_hook(_BASE, role="release", lang="de")) == _BASE


def test_blank_result_keeps_base():
    async def handler(**kw):
        return "   "

    register_hook("agent_system_prompt", handler)
    assert _run(_apply_agent_system_prompt_hook(_BASE, role="release", lang="de")) == _BASE


def test_raising_handler_is_fail_open():
    async def handler(**kw):
        raise RuntimeError("boom")

    register_hook("agent_system_prompt", handler)
    # run_hooks swallows handler errors → no result → base returned unchanged.
    assert _run(_apply_agent_system_prompt_hook(_BASE, role="release", lang="de")) == _BASE


def test_first_nonempty_result_wins():
    async def h_none(**kw):
        return None

    async def h_str(**kw):
        return "FIRST"

    register_hook("agent_system_prompt", h_none)
    register_hook("agent_system_prompt", h_str)
    out = _run(_apply_agent_system_prompt_hook(_BASE, role="x", lang="de"))
    assert out == "FIRST\n\n" + _BASE
