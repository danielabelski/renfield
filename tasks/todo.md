# Feature: Command Center — Phase 3 Fullscreen Kiosk (DONE)

Fullscreen ambient wall-display per the cinematic mockups
(`docs/design/assets/command-center/`). Deliberately breaks DESIGN.md's
restrained look — sanctioned in TODOS.md line 315 (glow aesthetic belongs on
the kiosk, stays out of the restrained admin board).

## Shipped
- [x] `KioskConstellation.tsx` — the GLOW variant (separate from restrained
      AgentConstellation): full-bleed dark cosmic field + stars, glowing bloom
      core with idle/listening/processing/speaking states + voice-burst ring,
      rings (roles/tools/rooms/peers), ambient telemetry corner, wordmark,
      clock, legend. Reduced-motion honoured. Content-free.
- [x] `useKioskModel.ts` — CommandCenterModel + voice-core state derived from
      the satellites' real `state` (idle/listening/processing/speaking) + room.
- [x] `KioskPage.tsx` — fullscreen, NO Layout chrome, hides cursor after 4s idle.
- [x] Route `/kiosk` in App.tsx OUTSIDE the Layout (AdminRoute; auth-off = open)
      + "Kiosk" link on the admin command-center header (opens in new tab).
- [x] i18n de/en (`kiosk.*`).
- [x] 3 RTL smoke tests (idle vs listening core state, telemetry counts).

## Verified
- [x] typecheck + lint + prod build green; 3/3 kiosk tests + guard tests pass.
- [x] Browser at 1920×1080: idle (crimson "BEREIT" core) + listening (turquoise
      "HÖRT ZU · Wohnzimmer" + voice-burst ring + active role beam), telemetry
      corner, clock, legend. Matches cc_stunning_smarthome / cc_voice_listening.
- [ ] Deploy frontend + prod check.

## Fast-follow — ambient tiles (DONE, 2026-07-03)
- [x] **Weather tile** — `GET /api/command-center/weather` (weather MCP, home
      location `KIOSK_WEATHER_LOCATION`, ~10-min TTL cache, self-hides). Reuses
      the chat weather artifact's WMO→icon map (`iconForCode` exported).
- [x] **Now-playing tile** — `GET /api/command-center/now-playing` off
      `MediaFollowService.active_sessions()` (one-per-room, PLAYING-only,
      content-minimal, no user ids). Bottom-center pills.
- [x] **Dynamic background** — drifting nebula + twinkling stars + slow radar
      sweep (all reduced-motion gated); no more flat black.
- [x] **Room-colour fix** — online-empty rooms no longer read as grey "unknown":
      online = turquoise (dim if empty), offline = crimson-dashed; legend redone.
- [x] Tests: backend TestKioskWeather/TestKioskNowPlaying/TestActiveSessions
      (60 passed on .159); frontend KioskPage tile tests (7 passed). Review clean.
- Skipped per user: next-public-Frist tile.

## Still open
Next public Frist tile (deferred by user); true circle-aware login-free
projection (only needed once prod runs multi-user — today it's auth-off / one
trust domain, kept content-free by design).

## Deploy note
`KIOSK_WEATHER_LOCATION` must be set in the prod ConfigMap (`renfield-env`) — it
is env-only (no real place name in git). Empty = weather tile hidden. Needs a
backend image build (new endpoints) + frontend build; `WEATHER_ENABLED` and
`MEDIA_FOLLOW_ENABLED` are already `true` in prod.
