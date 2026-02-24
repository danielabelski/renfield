---
name: deploy-production
description: Production deployment guide for Renfield. Covers Docker deployment, secrets management, satellite provisioning, and production safety rules. Triggers on "deploy", "Deployment", "production", "Produktion", "satellite deploy", "rsync", "renfield.local".
disable-model-invocation: true
---

# Production Deployment

## CRITICAL SAFETY

- **Pi Zero 2 W SD cards are EXTREMELY fragile** — SIGKILL during restart can brick the device
- **NEVER** run `docker-compose.prod.yml` on production — keine GPU auf PRD!
- **NEVER** change production config to make tests work — tests must work with existing production
- **Frontend:** `docker-compose.yml` has `target: development` — on PRD must build with production target manually

## Production Server

- Host: `renfield.local` at `/opt/renfield`
- No GPU — always use `docker compose up` (CPU-only), NOT `docker-compose.prod.yml`

## Deploy Workflow

```bash
# 1. Sync code (exclude secrets)
rsync -av --exclude='.env' --exclude='secrets/' --exclude='.git' --exclude='node_modules' ./ renfield.local:/opt/renfield/

# 2. Rebuild backend (on production server)
ssh renfield.local 'cd /opt/renfield && docker compose up -d --build'

# 3. Frontend (MUST use production target!)
ssh renfield.local 'cd /opt/renfield && \
  docker build --target production -t renfield-frontend src/frontend/ && \
  docker rm -f renfield-frontend && \
  docker run -d --name renfield-frontend --network renfield_renfield-network --network-alias frontend --restart unless-stopped renfield-frontend && \
  docker restart renfield-nginx'
```

**WARNING:** Do NOT use `docker compose up -d --build` for frontend on PRD — it builds the development target and breaks Nginx!

## Database Migrations

```bash
ssh renfield.local 'docker exec -it renfield-backend alembic upgrade head'
```

## See Also

- `references/secrets.md` — Docker secrets management
- `references/docker-variants.md` — Docker Compose configurations
- `references/satellite-deploy.md` — Satellite safety & provisioning
