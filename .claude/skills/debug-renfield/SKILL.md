---
name: debug-renfield
description: Troubleshooting guide for Renfield. Debug intent recognition, WebSocket issues, voice input, Home Assistant, satellites, and MCP connections. Triggers on "debug", "troubleshoot", "Fehler", "funktioniert nicht", "Problem", "broken", "Intent falsch", "WebSocket error".
---

# Troubleshooting Renfield

## Quick Diagnostic Commands

```bash
# Test intent recognition
curl -X POST "http://localhost:8000/debug/intent?message=YOUR_MESSAGE"

# Refresh HA keywords
curl -X POST "http://localhost:8000/admin/refresh-keywords"

# Re-embed all vectors (after changing embedding model)
curl -X POST "http://localhost:8000/admin/reembed"

# Check Ollama models
docker exec -it renfield-ollama ollama list

# Backend logs
docker compose logs -f backend

# Prometheus metrics (if enabled)
curl http://localhost:8000/metrics
```

## Common Issues (Quick Reference)

| Problem | First Check |
|---------|-------------|
| Wrong intent | `debug/intent` endpoint, refresh HA keywords |
| WebSocket fails | CORS in `main.py`, `VITE_WS_URL` match, backend logs |
| Voice not working | Whisper loads lazily (check logs), audio format (WAV/MP3/OGG) |
| HA integration | Token valid? Container network? Use IP not localhost |
| Satellite lost | Zeroconf: `docker compose logs backend \| grep zeroconf` |
| MCP tool fails | Check server enabled in `.env`, `docker compose logs backend` |

## See Also

- `references/intent-debug.md` — Intent recognition deep-dive
- `references/common-issues.md` — All known issues with fixes
- `references/admin-endpoints.md` — Admin/debug API endpoints
