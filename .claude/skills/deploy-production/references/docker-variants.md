# Docker Compose Variants

## Three Compose Files

| File | Use Case | GPU | Notes |
|------|----------|-----|-------|
| `docker-compose.yml` | Standard (CPU only) | No | Default for development and PRD |
| `docker-compose.dev.yml` | Development (Mac) | No | Hot-reload, volume mounts |
| `docker-compose.prod.yml` | Production (NVIDIA GPU) | Yes | SSL, NVIDIA Container Toolkit |

## Production (renfield.local)

**IMPORTANT: No GPU on production server!**

```bash
# Correct (CPU-only)
docker compose up -d

# WRONG — will fail (no GPU)
docker compose -f docker-compose.prod.yml up -d
```

## Development (Mac)

```bash
docker compose -f docker-compose.dev.yml up -d
```

## Frontend Build Targets

`docker-compose.yml` uses `target: development` by default. On production, the frontend must be built separately with the production target:

```bash
# Production frontend build
docker build --target production -t renfield-frontend src/frontend/
docker rm -f renfield-frontend
docker run -d --name renfield-frontend \
  --network renfield_renfield-network \
  --network-alias frontend \
  --restart unless-stopped \
  renfield-frontend
docker restart renfield-nginx
```

**NEVER** use `docker compose up -d --build` for frontend on PRD — it builds the development target.

## GPU Setup (other servers)

1. Install NVIDIA Container Toolkit
2. Use `docker-compose.prod.yml`
3. `Dockerfile.gpu` includes Node.js 20 for MCP stdio servers (`npx`)

## General Notes

- Fully offline once models are downloaded
- First startup: 5-10 min for model download
- Nginx (`profiles: [production]`) was started separately
- Upstream `frontend:80` = production build (not dev server on 3000)
