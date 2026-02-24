# Hook Insertion Points

Where hooks fire in the Renfield codebase.

## Insertion Point Table

| File | Hook Event | Execution Mode |
|------|-----------|----------------|
| `api/lifecycle.py` | `startup` | Awaited during startup (after all core services) |
| `api/lifecycle.py` | `register_routes` | Awaited during startup |
| `api/lifecycle.py` | `shutdown` | Awaited before MCP shutdown |
| `api/websocket/chat_handler.py` | `post_message` | Fire-and-forget background task |
| `api/websocket/chat_handler.py` | `retrieve_context` | Awaited, results appended to memory context |
| `services/rag_service.py` | `post_document_ingest` | Fire-and-forget after RAG ingest |
| `api/routes/chat_upload.py` | `post_document_ingest` | Fire-and-forget after text extraction |
| `services/agent_tools.py` | `register_tools` | Background task via `create_task` |

## Plugin Loading

Set `PLUGIN_MODULE=package.module:callable` in `.env`.

The callable is invoked at startup and should call `register_hook()`. Format: `module:function` (function receives no args).

**Loading code:** `api/lifecycle.py:_load_plugin_module()`

## Execution Modes

### Awaited
Hook is `await`ed — the caller waits for completion. Used for hooks that return data or must complete before proceeding.

### Fire-and-forget
Hook is scheduled as a background task via `asyncio.create_task()`. Caller does not wait. Used for post-processing that shouldn't slow down the response.

### Background task
Similar to fire-and-forget but with task tracking. Used for one-time registration during startup.

## Error Handling

Every hook call is wrapped in `try/except`. A failing hook:
- Logs the error (with hook name and traceback)
- Never crashes Renfield
- Never affects other hooks in the same event
