# Production Secrets Management

## Overview

Production uses Docker Compose file-based secrets (`/run/secrets/`) instead of `.env` for sensitive values. Secret files are in `/opt/renfield/secrets/` on the production server.

**Key rule:** Sensitive values (passwords, tokens, API keys) must NEVER appear in `.env` on production.

## How Secrets Work

1. **Pydantic Settings** — `secrets_dir="/run/secrets"` loads secret files into Settings fields
2. **MCP Server Injection** — `mcp_client.py` additionally injects `/run/secrets/*` into `os.environ` for:
   - YAML `${VAR}` substitution in `mcp_servers.yaml`
   - stdio subprocess environment variables

## Secret Files

Located at `/opt/renfield/secrets/` on the production server. Each file contains one secret value (no newline).

Examples:
- `secrets/ha_token` — Home Assistant long-lived access token
- `secrets/jwt_secret` — JWT signing secret
- `secrets/postgres_password` — PostgreSQL password

## Docker Compose Configuration

```yaml
secrets:
  ha_token:
    file: ./secrets/ha_token
  jwt_secret:
    file: ./secrets/jwt_secret

services:
  backend:
    secrets:
      - ha_token
      - jwt_secret
```

## Documentation

See `docs/SECRETS_MANAGEMENT.md` for the full guide.
