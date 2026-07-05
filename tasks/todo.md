# Phase 4 — Decommission the Command Center

**Boundary rule:** anything the kiosk needs MOVES to kiosk-owned code; command-center-only surface is DELETED.

## Backend
- [ ] Create `src/backend/api/websocket/kiosk_data.py` — move the shared read logic out of `command_center.py`: `KioskWeather` model, `_WEATHER_TTL_SECONDS`/`_weather_cache`/`compute_kiosk_weather`, `_weather_last_pushed`/`refresh_and_push_kiosk_weather`, `RoleActivityEntry` model + `_ACTIVITY_SCAN_WINDOW`/`recent_role_activity_entries`.
- [ ] Rewire `kiosk_handler.py` imports (`recent_role_activity_entries`, `compute_kiosk_weather`) → `kiosk_data`.
- [ ] Rewire `lifecycle.py` imports (`_WEATHER_TTL_SECONDS`, `refresh_and_push_kiosk_weather`) → `kiosk_data`.
- [ ] Delete `api/routes/command_center.py` (router + `/roles` `/activity` `/weather` `/now-playing` + `AgentRoleResponse`/`KioskNowPlaying` — no non-board consumer).
- [ ] `main.py`: remove `command_center` import (line 27) + `include_router` (line 201).
- [ ] Tests: rewrite `test_command_center_routes.py` → `test_kiosk_data.py` (test the moved functions, drop endpoint tests); update `test_kiosk_deltas.py`/`test_kiosk_handler.py` patch targets → `kiosk_data`.

## Frontend
- [ ] `git mv` the 4 kiosk files `components/command-center/{KioskConstellation.tsx,useKioskModel.ts,useKioskSocket.ts,types.ts}` → `components/kiosk/`.
- [ ] Delete `components/command-center/{AgentConstellation.tsx,useCommandCenterModel.ts,demoData.ts}` + `pages/CommandCenterPage.tsx`.
- [ ] New `api/resources/kiosk.ts` with the 4 types the kiosk uses (`AgentRoleInfo`, `RoleActivityEntry`, `KioskWeather`, `KioskNowPlaying`); delete `api/resources/commandCenter.ts` (its query hooks were command-center-only).
- [ ] Update imports: kiosk files + `KioskPage.tsx` → `components/kiosk/*` + `resources/kiosk`.
- [ ] `App.tsx`: drop `CommandCenterPage` lazy import + `/admin/command-center` route.
- [ ] `Layout.tsx`: swap nav `nav.commandCenter`→`/admin/command-center` for `nav.kiosk`→`/kiosk` (same `admin` permission) — REQUIRED, else `/kiosk` is unreachable.
- [ ] Tests: delete `CommandCenterPage.test.tsx`; update `useKioskSocket.test.tsx`/`KioskPage.test.tsx` import paths.

## i18n / docs
- [ ] i18n: add `nav.kiosk`, remove `nav.commandCenter`; keep `commandCenter.*` render keys the kiosk still uses (toolHint/legend/noSatellite); remove clearly board-only keys.
- [ ] `docs/design/command-center.md`: SUPERSEDED banner (keep the "why not poll" history).
- [ ] `CLAUDE.md`: rewrite the Command Center paragraph → kiosk-only push architecture (drop the six-admin-pages framing).
- [ ] `docs/FEATURES.md`: remove/merge §Command Center into the kiosk section.
- [ ] `tasks/kiosk-active-subsystem-plan.md`: mark phase 4 done.

## Verify
- [ ] `tsc --noEmit` (source + tests) + `npm run build` clean.
- [ ] Kiosk vitest green; no dead refs to command-center.
- [ ] Backend: grep for stray `command_center` refs; run kiosk/kiosk_data tests on .159.
