# Commit Format Reference

## Full Format

```
type(scope): Kurze Beschreibung (#issue)

Optionale längere Beschreibung.
Erklärt das "warum", nicht das "was".

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

## Type Reference

| Type | When to use | Example |
|------|-------------|---------|
| `feat` | New feature or capability | `feat(media): Add DLNA album playback (#45)` |
| `fix` | Bug fix | `fix(presence): Correct BLE hysteresis logic (#67)` |
| `docs` | Documentation only | `docs(api): Update WebSocket protocol docs (#12)` |
| `refactor` | Code restructuring, no behavior change | `refactor(mcp): Extract MCPManager base class (#89)` |
| `test` | Add or modify tests | `test(kg): Add entity resolution tests (#34)` |
| `chore` | Maintenance, deps, CI | `chore(deps): Update FastAPI to 0.110 (#56)` |

## Scope Conventions

Use the subsystem name as scope:
- `satellites`, `presence`, `media`, `kg` (knowledge graph)
- `mcp`, `rag`, `agent`, `auth`, `frontend`
- `api`, `ws` (websocket), `tts`, `stt`
- `ci`, `deps`, `docker`, `docs`

## Branch Naming

```
type/short-description
```

Examples:
- `feat/dlna-album-queue`
- `fix/presence-ble-flicker`
- `docs/update-claude-md`

## Important Notes

- Branch protection: Direct push to `main` is BLOCKED by GitHub
- Always create PR via `gh pr create`
- Issue number is REQUIRED in commit message
- Documentation MUST be updated before push
