"""
Agent Tool Registry — Wraps existing infrastructure as tool descriptions for the LLM.

Generates compact tool descriptions from:
- MCP servers (Home Assistant, n8n, weather, search, etc.)
- Internal tools (room resolution, media playback)

These descriptions are included in the Agent Loop prompt so the LLM knows
which tools it can call.

Tool filtering is handled by AgentRouter which classifies messages into
roles (smart_home, research, documents, etc.) with pre-defined MCP server lists.
"""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from services.mcp_client import MCPManager


def _synthesize_schema(parameters: dict[str, str]) -> dict:
    """Synthesize a JSON Schema from a flat {param_name: description} dict.

    Used for internal tools and plugin tools that don't have a full JSON Schema.
    Parameters with '(required)' in the description are marked as required.
    """
    if not parameters:
        return {"type": "object", "properties": {}}

    properties = {}
    required = []
    for name, desc in parameters.items():
        is_required = "(required)" in desc
        clean_desc = desc.replace(" (required)", "").replace("(required)", "").strip()
        properties[name] = {"type": "string", "description": clean_desc}
        if is_required:
            required.append(name)

    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


@dataclass
class ToolDefinition:
    """Definition of a tool available to the Agent."""
    name: str
    description: str
    parameters: dict[str, str] = field(default_factory=dict)  # param_name -> description
    input_schema: dict | None = None  # Full JSON Schema (from MCP or synthesized)


class AgentToolRegistry:
    """Registry of all tools available to the Agent Loop.

    Tools are registered dynamically from:
    - MCP servers (Home Assistant, n8n, weather, search, etc.)
    - Internal tools (room resolution, media playback)
    """

    def __init__(
        self,
        mcp_manager: Optional["MCPManager"] = None,
        server_filter: list[str] | None = None,
        internal_filter: list[str] | None = None,
    ):
        """Initialize the tool registry.

        Args:
            mcp_manager: MCP server manager
            server_filter: If set, only include MCP tools from these server names.
                          None means include all servers.
            internal_filter: If set, only include these internal tool names.
                            None means include all internal tools.
        """
        self._tools: dict[str, ToolDefinition] = {}
        self.server_filter = server_filter  # Exposed for hooks to do role-aware filtering

        # Register MCP tools (includes HA, n8n, weather, search, etc.)
        if mcp_manager:
            self._register_mcp_tools(mcp_manager, server_filter=server_filter)

        # Register internal agent tools (room resolution, media playback)
        self._register_internal_tools(internal_filter=internal_filter)

        # Hook: register_tools — plugins can add their own tool definitions
        self._schedule_register_tools_hook()

    def _register_internal_tools(self, internal_filter: list[str] | None = None) -> None:
        """Register internal agent tools (room resolution, media playback).

        Args:
            internal_filter: If set, only register these tool names. None = all.
        """
        from services.internal_tools import InternalToolService

        for name, definition in InternalToolService.TOOLS.items():
            if internal_filter is not None and name not in internal_filter:
                continue

            params = {}
            for param_name, param_desc in definition.get("parameters", {}).items():
                params[param_name] = param_desc

            tool = ToolDefinition(
                name=name,
                description=definition["description"],
                parameters=params,
            )
            self._tools[tool.name] = tool
            logger.debug(f"Internal agent tool registered: {tool.name}")

    def _schedule_register_tools_hook(self) -> None:
        """Fire the register_tools hook so plugins can add tool definitions."""
        import asyncio

        from utils.hooks import run_hooks

        async def _run():
            await run_hooks("register_tools", registry=self)

        try:
            loop = asyncio.get_running_loop()
            # Store reference to prevent GC (RUF006)
            self._hook_task = loop.create_task(_run())
        except RuntimeError:
            pass  # No event loop (e.g. sync tests) — skip

    def _register_mcp_tools(self, mcp_manager: "MCPManager", server_filter: list[str] | None = None) -> None:
        """Register MCP tools as agent tools.

        Args:
            mcp_manager: MCP server manager
            server_filter: If set, only include tools from these server names. None = all.
        """
        for mcp_tool in mcp_manager.get_all_tools():
            if server_filter is not None and mcp_tool.server_name not in server_filter:
                continue
            params = {}
            schema_props = mcp_tool.input_schema.get("properties", {})
            required_params = mcp_tool.input_schema.get("required", [])

            for param_name, param_schema in schema_props.items():
                desc = param_schema.get("description", param_schema.get("type", ""))
                if param_name in required_params:
                    desc += " (required)"
                params[param_name] = desc

            tool = ToolDefinition(
                name=mcp_tool.namespaced_name,
                description=mcp_tool.description,
                parameters=params,
                input_schema=mcp_tool.input_schema or None,
            )
            self._tools[tool.name] = tool
            logger.debug(f"MCP agent tool registered: {tool.name}")

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Get a tool definition by name."""
        return self._tools.get(name)

    def get_tool_names(self) -> list[str]:
        """Get list of all registered tool names."""
        return list(self._tools.keys())

    def resolve_tool_name(self, name: str) -> str | None:
        """Resolve a tool name, supporting short names without namespace prefix.

        Small LLMs sometimes emit 'GetLiveContext' instead of
        'mcp.homeassistant.GetLiveContext'. This tries exact match first,
        then falls back to suffix match (must be unambiguous).
        """
        if name in self._tools:
            return name
        # Suffix match: find tools ending with '.<name>'
        suffix = f".{name}"
        matches = [k for k in self._tools if k.endswith(suffix)]
        if len(matches) == 1:
            return matches[0]
        return None

    def is_valid_tool(self, name: str) -> bool:
        """Check if a tool name is valid."""
        return self.resolve_tool_name(name) is not None

    def build_tools_schema(self, tools: dict[str, "ToolDefinition"] | None = None) -> list[dict]:
        """Build OpenAI-compatible function calling schema from registered tools.

        Tool names are sanitized for API compatibility (dots → double underscores)
        since OpenAI requires names matching ``^[a-zA-Z0-9_-]+$``.
        Use ``unsanitize_tool_name()`` to map sanitized names back to originals.

        Returns:
            List of tool definitions in OpenAI format:
            [{"type": "function", "function": {"name": ..., "description": ..., "parameters": {JSON Schema}}}]
        """
        tool_set = tools if tools is not None else self._tools
        self._sanitized_names: dict[str, str] = {}  # sanitized → original
        result = []
        for tool in tool_set.values():
            schema = tool.input_schema if tool.input_schema else _synthesize_schema(tool.parameters)
            safe_name = tool.name.replace(".", "__")
            self._sanitized_names[safe_name] = tool.name
            result.append({
                "type": "function",
                "function": {
                    "name": safe_name,
                    "description": tool.description,
                    **({"parameters": schema} if schema else {}),
                },
            })
        return result

    def unsanitize_tool_name(self, name: str) -> str:
        """Map a sanitized tool name back to the original (dots restored).

        Falls back to simple ``__`` → ``.`` replacement if the name wasn't
        in the sanitization map (e.g. for locally registered tools without dots).
        """
        if hasattr(self, "_sanitized_names") and name in self._sanitized_names:
            return self._sanitized_names[name]
        # Fallback: reverse the sanitization
        return name.replace("__", ".")

    def build_tools_prompt(self, tools: dict[str, "ToolDefinition"] | None = None) -> str:
        """
        Build a compact text description of tools for the LLM prompt.

        Args:
            tools: Optional dict of tools to include. If None, uses all registered tools.

        Returns:
            Formatted string listing tools with parameters.
        """
        tool_set = tools if tools is not None else self._tools
        if not tool_set:
            return "KEINE TOOLS VERFÜGBAR."

        lines = ["VERFÜGBARE TOOLS:"]

        for tool in tool_set.values():
            if tool.parameters:
                params_str = ", ".join(
                    f"{name}: {desc}" for name, desc in tool.parameters.items()
                )
                lines.append(f"- {tool.name}: {tool.description} | Parameter: {{{params_str}}}")
            else:
                lines.append(f"- {tool.name}: {tool.description}")

        return "\n".join(lines)
