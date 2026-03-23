# Benutzer-Persoenlichkeit

## Uebersicht

Renfield unterstuetzt einen konfigurierbaren **Kommunikationsstil** pro Benutzer. Jeder User kann einen vordefinierten Stil waehlen und optional per Freitext feinjustieren. Der Stil wird in den System-Prompt injiziert und beeinflusst alle Antworten — sowohl im direkten Chat als auch im Agent-Loop.

## Konfiguration

### Vordefinierte Stile (`personality_style`)

| Stil | Beschreibung |
|------|-------------|
| `freundlich` (Default) | Warmherzig, hilfsbereit, ausfuehrlich. Zeigt Empathie und Interesse. |
| `direkt` | Kurz, praezise, ohne Floskeln. Kommt direkt zum Punkt. |
| `formell` | Hoeflich und professionell. Verwendet Siezen. |
| `casual` | Locker und ungezwungen, wie unter Freunden. |

### Freitext-Feintuning (`personality_prompt`)

Optionales Freitextfeld fuer individuelle Anweisungen, z.B.:
- "Verwende Emojis in deinen Antworten"
- "Erklaere technische Begriffe immer ausfuehrlich"
- "Antworte immer mit einer kurzen Zusammenfassung am Anfang"

Der Freitext wird dem vordefinierten Stil angehaengt.

## Technische Umsetzung

### Datenbank

Zwei neue Felder auf dem `User`-Model:

```python
personality_style = Column(String(20), default="freundlich", nullable=False, server_default="freundlich")
personality_prompt = Column(Text, nullable=True)
```

### Prompt-Injection

Der Persoenlichkeits-Kontext wird als eigener Block in den System-Prompt eingefuegt — nach dem Base-Prompt, vor Memory-Kontext:

```
[Base System Prompt (chat.yaml)]
[KOMMUNIKATIONSSTIL: ...]         <-- NEU
[ERINNERUNGEN: ...]
[DOKUMENT-KONTEXT: ...]
```

Gilt fuer alle Pfade:
- Direkter Chat (`ollama_service.chat_stream`)
- Agent-Loop (`agent_service._build_agent_prompt`)
- RAG-Antworten (`_stream_rag_response`)

### Prompt-Templates

Stil-Beschreibungen und Context-Template in `src/backend/prompts/chat.yaml` (de + en).
Agent-Prompts in `src/backend/prompts/agent.yaml` erhalten die Variable `{personality_context}`.

### API

Die bestehenden User-Routen (`/api/users`) werden erweitert:
- `GET /api/users` — Response enthaelt `personality_style`, `personality_prompt`
- `PATCH /api/users/{id}` — Akzeptiert `personality_style`, `personality_prompt`
- `POST /api/users` — Akzeptiert `personality_style` (default: "freundlich"), `personality_prompt`

### Frontend

UsersPage.jsx erhaelt im Create/Edit-Modal:
- Dropdown fuer `personality_style`
- Textarea fuer `personality_prompt`

## Dateien

| Datei | Aenderung |
|-------|-----------|
| `src/backend/models/database.py` | Neue Felder auf User |
| `alembic/versions/..._add_user_personality.py` | Migration |
| `src/backend/prompts/chat.yaml` | Personality Templates + Stil-Texte |
| `src/backend/prompts/agent.yaml` | `{personality_context}` Variable |
| `src/backend/services/ollama_service.py` | `get_system_prompt()` + `chat_stream()` erweitern |
| `src/backend/services/agent_service.py` | `_build_agent_prompt()` + `run()` erweitern |
| `src/backend/api/websocket/chat_handler.py` | Personality laden und durchreichen |
| `src/backend/api/routes/users.py` | Request/Response Models + Handler |
| `src/backend/api/routes/auth.py` | Auth UserResponse erweitern |
| `src/frontend/src/pages/UsersPage.jsx` | Dropdown + Textarea |
| `src/frontend/src/i18n/locales/de.json` | Uebersetzungen |
| `src/frontend/src/i18n/locales/en.json` | Uebersetzungen |
