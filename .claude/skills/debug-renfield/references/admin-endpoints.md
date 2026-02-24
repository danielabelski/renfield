# Admin & Debug API Endpoints

## Debug Endpoints

### Test Intent Recognition
```bash
curl -X POST "http://localhost:8000/debug/intent?message=Schalte das Licht ein"
```
Returns the parsed intent with confidence scores and ranked alternatives.

## Admin Endpoints

### Refresh Home Assistant Keywords
```bash
curl -X POST "http://localhost:8000/admin/refresh-keywords"
```
Fetches device names from Home Assistant to improve intent recognition accuracy.

### Re-Embed All Vectors
```bash
curl -X POST "http://localhost:8000/admin/reembed"
```
After changing the embedding model (`OLLAMA_EMBED_MODEL`), recalculates all existing vectors:
- RAG chunks
- Conversation memories
- Intent corrections
- Notification suppressions

Runs in background — check logs for progress.

### Prometheus Metrics
```bash
# Requires METRICS_ENABLED=true
curl http://localhost:8000/metrics
```
Returns Prometheus exposition format metrics.

## Paperless Audit Endpoints (if enabled)

```bash
POST /api/admin/paperless-audit/start    # Start audit run
GET  /api/admin/paperless-audit/status   # Check status
GET  /api/admin/paperless-audit/results  # Get results
POST /api/admin/paperless-audit/apply    # Apply suggestions
POST /api/admin/paperless-audit/skip     # Skip document
GET  /api/admin/paperless-audit/stats    # Statistics
POST /api/admin/paperless-audit/re-ocr   # Re-OCR document
```

## Knowledge Graph Endpoints (if enabled)

```bash
GET  /api/knowledge-graph/entities       # List entities
GET  /api/knowledge-graph/relations      # List relations
POST /api/knowledge-graph/cleanup/invalid          # Scan invalid entities
GET  /api/knowledge-graph/cleanup/duplicates       # Find duplicates
POST /api/knowledge-graph/cleanup/merge-duplicates # Auto-merge
```

## Log Inspection

```bash
# Backend logs
docker compose logs -f backend

# Ollama logs
docker compose logs -f ollama

# All services
docker compose logs -f

# Zeroconf (satellite discovery)
docker compose logs backend | grep zeroconf
```
