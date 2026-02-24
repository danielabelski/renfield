---
name: add-integration
description: Guide for adding a new MCP server integration to Renfield. Covers YAML configuration, transport types, permission setup, prompt filtering, and notification polling. Triggers on "add integration", "neue Integration", "MCP server hinzufuegen", "new service", "neuen Service einbinden".
---

# Adding a New Integration

All external integrations run via MCP servers. No code changes needed — just YAML config.

## Quick Start (3 Steps)

### 1. Deploy an MCP server

HTTP/SSE or stdio transport. The server must implement the MCP protocol.

### 2. Add to `config/mcp_servers.yaml`

```yaml
servers:
  - name: your_service
    url: "${YOUR_SERVICE_MCP_URL:-http://localhost:9090/mcp}"
    transport: streamable_http
    enabled: "${YOUR_SERVICE_ENABLED:-true}"
    refresh_interval: 300
    example_intent: mcp.your_service.main_tool
    prompt_tools:
      - main_tool
      - secondary_tool
    examples:
      de: ["Beispiel-Anfrage auf Deutsch"]
      en: ["Example query in English"]
```

### 3. Done

Tools are auto-discovered as `mcp.your_service.<tool_name>` intents. `ActionExecutor` routes `mcp.*` intents to `MCPManager.execute_tool()` automatically.

## Transport Types

| Transport | URL Format | Use Case |
|-----------|-----------|----------|
| `streamable_http` | `http://host:port/mcp` | Recommended for HTTP servers |
| `sse` | `http://host:port/sse` | Legacy SSE servers |
| `stdio` | N/A (use `command` + `args`) | Local subprocess (npx, python) |

### stdio Example

```yaml
- name: local_service
  transport: stdio
  command: npx
  args: ["-y", "@some/mcp-server"]
  enabled: "${LOCAL_SERVICE_ENABLED:-false}"
  env:
    API_KEY: "${LOCAL_SERVICE_API_KEY}"
```

## Prompt Tool Filtering

With 100+ MCP tools across 8+ servers, not all tools should appear in the intent prompt:
- `prompt_tools`: List tool base names to include in LLM intent prompt
- Omit field = show ALL tools in prompt
- All tools remain executable regardless of `prompt_tools`

## See Also

- `references/yaml-fields.md` — Complete YAML field reference
- `references/permissions.md` — MCP permission system
