# Intent Recognition Debugging

## How Intent Recognition Works

The core system in `src/backend/services/ollama_service.py`:

1. **extract_intent()** — Parses natural language into structured intents (returns top intent)
2. **extract_ranked_intents()** — Returns ranked list of 1-3 intents sorted by confidence (fallback chain)
3. **Dynamic Keyword Matching** — Fetches device names from Home Assistant to improve accuracy

## Intent Types

- `mcp.*` — All external integrations via MCP servers (HA, n8n, weather, search, news, etc.)
- `knowledge.*` — Knowledge base / RAG queries (only for user's own documents)
- `general.conversation` — Normal chat, general knowledge, smalltalk (no action needed)

## Ranked Intents & Fallback Chain

The LLM returns up to 3 weighted intents. The chat handler tries them in order:
- If one fails (e.g., RAG returns 0 results), falls through to the next
- If all fail and Agent Loop is enabled, it kicks in as final fallback

## MCP Tool Prompt Filtering

With 100+ MCP tools across 8+ servers, the intent prompt uses `prompt_tools` (from `mcp_servers.yaml`) to show only the most relevant tools per server. This reduces the prompt to ~20 tools while keeping all tools available for execution.

See `IntentRegistry.build_intent_prompt()`.

## Intent Feedback Learning

Renfield learns from user corrections using a 3-scope feedback system with pgvector semantic matching:
- Scopes: `intent` (wrong classification), `agent_tool` (wrong tool), `complexity` (wrong simple/complex)
- Corrections stored with 768-dim embeddings
- Injected as few-shot examples on similar queries (cosine similarity threshold: 0.75)

Key files: `services/intent_feedback_service.py`, `api/routes/feedback.py`, `components/IntentCorrectionButton.jsx`

## Debug Steps

1. Test with debug endpoint:
   ```bash
   curl -X POST "http://localhost:8000/debug/intent?message=Schalte das Licht ein"
   ```

2. Check if HA keywords are loaded:
   ```bash
   curl -X POST "http://localhost:8000/admin/refresh-keywords"
   ```

3. Verify Ollama model:
   ```bash
   docker exec -it renfield-ollama ollama list
   ```

4. Check intent prompt: Look at `prompts/intent.yaml` and `config/mcp_servers.yaml` for `prompt_tools`

5. Check feedback corrections: Query `intent_corrections` table for conflicting entries
