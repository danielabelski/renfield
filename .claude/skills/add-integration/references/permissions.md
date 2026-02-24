# MCP Permission System

## Overview

Dynamic permission strings control MCP tool access. Hybrid system combining convention-based and YAML-configured permissions.

## Permission Hierarchy

```
kb.all > kb.shared > kb.own > kb.none
ha.full > ha.control > ha.read > ha.none
cam.full > cam.view > cam.none
mcp.* > mcp.<server>.* > mcp.<server>.<tool>
```

## Permission Types

### Convention-based (no YAML needed)

Server `weather` auto-requires `mcp.weather` permission. No YAML config needed.

### YAML Granular

```yaml
# Server-level
permissions: ["mcp.calendar.read", "mcp.calendar.manage"]

# Per-tool (takes priority)
tool_permissions:
  list_events: "mcp.calendar.read"
  create_event: "mcp.calendar.manage"
```

### Wildcards

- `mcp.*` — All MCP tools
- `mcp.calendar.*` — All calendar tools

## Resolution Order

1. `user_permissions = None` → **allow** (no auth / unauthenticated)
2. User has `mcp.*` → **allow**
3. `tool_permissions` match → check specific permission
4. `permissions` (server-level) match → user needs at least one
5. Convention `mcp.<server>` match → check
6. No match → **deny**

## Default Roles

| Role | MCP | HA | KB | Cam |
|------|-----|----|----|-----|
| Admin | `mcp.*` | `ha.full` | `kb.all` | `cam.full` |
| Familie | `mcp.*` | `ha.full` | `kb.shared` | `cam.view` |
| Gast | none | `ha.read` | `kb.none` | `cam.none` |

## User-ID Propagation

`user_id` flows through: `chat_handler` → `ActionExecutor.execute()` → `MCPManager.execute_tool()` → MCP server tool parameters.

MCP servers receive `user_id` and can use it for per-user filtering (e.g. calendar visibility).

## Key Files

- `models/permissions.py` — Permission model and hierarchy
- `services/auth_service.py` — JWT auth service
- `services/mcp_client.py` — Permission checking in MCPManager
- `docs/ACCESS_CONTROL.md` — Full documentation
