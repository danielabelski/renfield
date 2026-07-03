# Feature: Command Center — live constellation (Phase 1+2)

Primary source: `docs/design/command-center.md`. Prototype exists
(`src/frontend/src/components/command-center/`), unrouted, demo-fed.
Goal: the routed, live, admin-gated `/admin/command-center` — stunning
**within DESIGN.md** (warm editorial, no glowing-orb slop).

## Reality corrections vs the design doc (from exploration)
- `/api/roles` = RBAC system roles, NOT agent roles. Agent roles live only in
  `app.state.agent_router.roles` (loaded from `config/agent_roles.yaml`) —
  **no REST endpoint exists** → add one (read-only, ADMIN).
- The chat WS is chat-page-local (`useChatWebSocket`, no global WS context) and
  per-session — an admin page riding it would only ever see *its own* turns.
  → Better: poll a tiny content-free **activity endpoint** reading
  `messages.message_metadata->>'agent_role'` (persisted by role-surfacing,
  chat_handler:2211). Household-wide pulse, kiosk-safe (role + timestamp only).
- Tools ring source: `GET /api/mcp/status` (per-server connected/last_error)
  blended with `GET /api/tool-health` (failure rates) for degraded.
- Rooms ring: `GET /api/satellites` (online) + `GET /api/presence/rooms` (occupants).
- Peers: `GET /api/federation/peers` (last_seen_at).

## Backend (new, small, read-only)
- [ ] `api/routes/command_center.py` (prefix `/api/command-center`, ADMIN-gated,
      rate-limited like tool_health.py):
      - `GET /roles` → `[{name, description{de,en}, mcp_servers, internal_tools}]`
        from `request.app.state.agent_router.roles` (None-safe → []).
      - `GET /activity?limit=` → `[{role, at, ok}]` newest-first from assistant
        messages' `message_metadata.agent_role` (+ `action_success`), dialect-safe
        (fetch window, extract in Python). NO content, NO user ids.
- [ ] Mount in `main.py`.
- [ ] Tests `tests/backend/test_command_center.py` (run on .159).

## Frontend
- [ ] `api/resources/commandCenter.ts` — `useAgentRolesQuery` (CONFIG stale),
      `useRoleActivityQuery` (refetchInterval 3s); `keys.commandCenter.*`.
- [ ] `components/command-center/useCommandCenterModel.ts` — compose the model
      from the 6 queries; per-ring loading/empty/error; health mapping
      (connected+clean=healthy, connected+last_error|success_rate<0.8=degraded,
      disconnected=down, no-data=unknown).
- [ ] Elevate `AgentConstellation.tsx`:
      - live pulse + decaying trail (last N activations, opacity decay)
      - hover role↔tool edges from the role's `mcp_servers`/`internal_tools`
      - click drill-downs (roles→/admin/routing, tools→/admin/integrations,
        rooms→/admin/satellites, peers→/brain/audit), keyboard-focusable
      - all four interaction states; reduced-motion; DESIGN.md tokens only
- [ ] `pages/CommandCenterPage.tsx` — page shell, constellation board,
      live activity rail (content-free), narrow-width list fallback (<lg),
      offline/"system busy" calm state.
- [ ] Route in `App.tsx` (+lazy) + nav in `Layout.tsx` + i18n de/en
      (nav key + new commandCenter.* additions in BOTH locales).
- [ ] Vitest RTL tests `tests/frontend/react/pages/CommandCenterPage.test.tsx`.

## Verification loop (iterate until perfect)
- [ ] `npm run typecheck` + targeted vitest.
- [ ] `npm run build` (prod Tailwind pass).
- [ ] Browser walkthrough (light + dark, wide + narrow, reduced-motion).
- [ ] `/review` + docs sweep (CLAUDE.md, docs/FEATURES.md, TODOS.md) before merge.

## Deliberately NOT in scope (per design doc)
Write actions; Phase-3 kiosk authz; always-on role↔tool edges (hover only);
WS transport for the pulse (polling is household-wide + kiosk-safe; the chat WS
is per-session and page-local).
