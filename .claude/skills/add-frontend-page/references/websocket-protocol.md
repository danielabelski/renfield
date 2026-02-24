# WebSocket Protocol Reference

## Frontend Connection Architecture

The frontend uses **two independent WebSocket connections**:

| Connection | Endpoint | Purpose |
|------------|----------|---------|
| **Chat WS** | `/ws` | Send/receive chat messages, conversation persistence via `session_id` |
| **Device WS** | `/ws/device` | Device registration, room assignment, capabilities |

These are completely independent — chat works without device registration, but room context requires it.

## Chat WebSocket (`/ws`)

### Client → Server

```json
{
  "type": "text",
  "content": "Schalte das Licht im Wohnzimmer ein",
  "session_id": "session-1234567890-abc123def",
  "use_rag": false,
  "knowledge_base_id": null
}
```

### Server → Client (streaming)

```json
{"type": "action", "intent": {...}, "result": {...}}
{"type": "stream", "content": "Ich habe..."}
{"type": "done", "tts_handled": false}
```

### Agent Loop Messages

```json
{"type": "agent_thinking"}
{"type": "agent_tool_call", "tool": "...", "params": {...}, "reason": "..."}
{"type": "agent_tool_result", "success": true, "data": {...}}
{"type": "stream", "content": "Final answer..."}
{"type": "done", "agent_steps": 3}
```

### Session Persistence

When `session_id` is provided:
- History loaded from DB (up to 10 messages)
- Each exchange is saved automatically

## Device WebSocket (`/ws/device`)

Used for device registration, room assignment, and capabilities. See `src/backend/main.py` for protocol details.

## Satellite WebSocket (`/ws/satellite`)

Used for audio streaming, STT, TTS. See `src/backend/api/websocket/satellite_handler.py` for protocol details.
