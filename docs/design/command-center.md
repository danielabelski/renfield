# Command Center — live constellation of the running system

Status: **Phase 1+2 IMPLEMENTED** (2026-07, `feature/command-center-phase1`):
`/admin/command-center` is routed (`<AdminRoute>` + nav entry), fed live by
`useCommandCenterModel`, with drill-downs, a decaying pulse trail, hover
reach-edges, a content-free activity rail, and a grouped-list fallback below
`lg`. Phase 3 (kiosk) remains open.

Two reality corrections vs the plan below, discovered during implementation:

1. **Agent roles had no REST surface** — `/api/roles` is RBAC system roles,
   not agent roles. New read-only ADMIN endpoint
   `GET /api/command-center/roles` exposes `app.state.agent_router.roles`
   (name, localized description, `mcp_servers`/`internal_tools` — the latter
   back the hover reach-edges).
2. **The chat WS can't drive the pulse** — it is chat-page-local
   (`useChatWebSocket`, no global WS context) and per-session, so an admin
   page riding it would only see its *own* turns. Instead the board polls
   `GET /api/command-center/activity` (3s): recent role activations read from
   the persisted `message_metadata.agent_role` (role-surfacing). Content-free
   by construction (role + timestamp + action_success only — no message text,
   no user ids), household-wide, and kiosk-safe — strictly better than the WS
   idea for this surface.

Scope: a single **read-first "mission control"** surface that shows, at a glance,
what the running Renfield is *doing right now* — which agent role is answering,
which MCP tools/integrations are healthy, which satellites/rooms are live and
occupied, and which federation peers are reachable. It does **not** add control
logic; it composes data Renfield already emits and drills down into the existing
admin pages.

## Why this doc exists

The trigger was a reference UI ("Apex" by Reznikov Engineering): a radial
"mission control" with a glowing central core surrounded by labelled agents
(Strategist, Researcher, Chief of staff, …) and tools (Calendar, Email, Memory,
Drive). The pattern is compelling because it answers one question instantly:
**"what is my agent system, and what is it doing?"**

Renfield already produces every datum such a view needs — but the data is
scattered across six separate admin pages (`/admin/routing`,
`/admin/tool-health`, `/admin/trajectories`, `/admin/satellites`,
`/admin/presence`, `/admin/integrations`). No surface unifies them, and none is
*alive* (reflecting the current turn as it happens). The Command Center is that
unifying, live surface.

## The reference, and what to borrow vs reject

**Borrow** (the interaction concept):

- A **central core** = the orchestrator/assistant, with the **currently-active
  agent role** surfaced on it live.
- **Concentric rings of labelled nodes** = the system's capabilities, grouped by
  kind (roles, tools, rooms, peers).
- **Node state encodes live status** (health / presence / online) by colour +
  a non-colour channel.
- An **ambient, at-a-glance** read — legible from across the room, good on a
  wall tablet / kiosk.

**Reject** (the aesthetic): the Apex look is a glowing sci-fi orb with energy
blooms and radial gradients. **This directly violates `DESIGN.md`**, which is
locked and explicit:

> Decoration level: INTENTIONAL — light grain on cream surfaces, **no decorative
> blobs / gradients / icons-in-circles**.

and the AI-slop blacklist forbids *"purple/violet/indigo gradients"* and
*"icon-in-circle decoration"*. Renfield's identity is **warm, restrained,
serif** — crimson + turquoise + cream + Cormorant, "for HOME, not work." So the
Command Center must be a **structural constellation**, not a light show:

| Apex (reject) | Renfield Command Center (adopt) |
|---|---|
| Glowing energy orb, radial gradient core | Solid crimson core disc, thin ring, Cormorant wordmark |
| Bloom/particle connectors | Thin 1–2px connectors; only the **active** edge animates (a subtle dash), `prefers-reduced-motion` honoured |
| Sci-fi neon palette | DESIGN.md tier/brand tokens only (`--color-primary-*`, `--color-accent-*`, cream) |
| Decorative; impressionistic | Legible; every node is a real entity that drills down |

This is the central design decision: **the *layout* is borrowed, the *vibe* is
Renfield's.** Calibrate every pixel against `DESIGN.md` at build time.

## The constellation: rings and what feeds them

```
                    ◦ peers (federation, outer arc)
              ○────────────────────────────────○
           ○        rooms / satellites            ○
        ○        ◇──────────────────────◇           ○
      ◇       tools / MCP integrations      ◇         ◦
     ◇     ●──────────────────────────●       ◇
    ◇     ●      agent roles            ●      ◇
    ◇    ●         ┌─────────┐          ●     ◇
         ●         │ RENFIELD│  ← active role surfaced here
    ◇    ●         │  core   │          ●     ◇
    ◇     ●        └─────────┘         ●      ◇
     ◇     ●──────────────────────────●      ◇
      ◇        ◇──────────────────────◇     ◦
        ○         (rooms ring)            ○
           ○                            ○
              ○────────────────────────○
```

| Ring | Nodes | Live state shown | Backend source (already exists) |
|---|---|---|---|
| **Core** | Renfield orchestrator | the **currently active agent role** | WS `done` frame `agent_role` + `role_hint` (role-surfacing item 6, already on the wire) |
| **Roles** | `agent_roles.yaml` roles (smart_home, knowledge, media, presence, general, conversation, …) | which role just answered (pulse), idle vs active | `GET /api/roles`; live highlight from the chat WS `done` frame |
| **Tools / MCP** | Home Assistant, Paperless, Jellyfin, Calendar, Email, Search, n8n, DLNA, Weather, News, Radio | health: healthy / degraded / down / unknown | `GET /api/tool-health`, `GET /api/intents/integrations/summary` |
| **Rooms / satellites** | each satellite/room | online/offline + occupant count (presence) | `GET /api/satellites`, `GET /api/presence/rooms` |
| **Peers** (optional outer arc) | federation instances | reachable / unreachable | federation status endpoint (Federation Audit already lists peers) |

Node colour encoding (DESIGN.md tokens, **always paired with a non-colour
channel** per WCAG 1.4.1 — a ring style, icon, or label, never colour alone):

- **Healthy / online / occupied** → `--color-accent-500` turquoise.
- **Degraded / warn** → `--color-primary-300` light crimson.
- **Down / offline** → `--color-primary-700` deep crimson, **plus** a dashed/hollow ring.
- **Unknown / idle** → `--color-gray-400`, hollow.
- **Active role** (this turn) → turquoise fill + the only animated connector.

## Live data

Two channels, no new inference, no new polling burden:

1. **Push (the "alive" part):** the Command Center subscribes to the same chat WS
   stream the app already maintains. On each `done` frame it reads `agent_role`
   and lights that role node + pulses its connector for ~2s. This is the cheap,
   high-impact signal — it makes the board *breathe* with real household activity
   without any extra backend work (role-surfacing already plumbed it).
2. **Poll (the "status" part):** tool-health, satellites, presence, and peers
   poll on the existing React-Query cadence (`refetchInterval`, e.g. 5s for
   presence/satellites which already auto-refresh, 30s for tool-health). No new
   endpoints — all six already exist and are admin-gated.

## Interaction model

**Read-first.** Every node is a **drill-down**, not a control:

- Click a **role** → `/admin/routing` (+ optional pin-role-for-next-turn, reusing
  the existing routing-only `role_hint`; *display* and *pin* are both already
  permission-safe — pinning never escalates, every tool stays gated at execute).
- Click a **tool/MCP** → `/admin/tool-health` (or `/admin/integrations`) filtered
  to that tool.
- Click a **satellite/room** → `/admin/satellites/{id}` or `/admin/presence`.
- Click a **peer** → `/brain/audit` (Federation Audit).

No write actions in v1. (The interactive device widgets already own the
artifact→action write-back channel inside chat; the Command Center stays an
**awareness** surface, which keeps its security surface trivial.)

## Permissions & circles

- **v1 is `Permission.ADMIN`-gated** (route `/admin/command-center`, same
  `<AdminRoute>` + backend `require_permission(ADMIN)` as the six pages it
  composes). No new authz.
- **A future household/kiosk "ambient" mode** (Phase 3) would need a non-admin,
  **circle-aware** projection: a household member sees rooms/presence they're
  permitted to and *never* sees another member's private activity. That is a real
  authz design (reuse the presence service's existing per-user room visibility +
  `circle_sql` rules for anything atom-bearing) — explicitly **out of v1**.
- The live role pulse must **not** leak *content* (no message text, no who-asked
  on a shared display) — it shows only the *role* and *node status*. Keep it that
  way for the kiosk story.

## Interaction states (not designed until these are)

Per the DESIGN.md / chat-roadmap discipline, every node has all four:

| Node | loading | empty | error | live |
|---|---|---|---|---|
| Core | skeleton disc | n/a (always present) | "backend unreachable" banner | active-role label |
| Role | dim outline | role list empty → hide ring | role fetch failed → grey ring + retry | turquoise + pulse |
| Tool | dim outline | no tools configured → hide ring | health fetch failed → all `unknown` | health colour |
| Room/sat | dim outline | **no satellites → warm empty state, not blank** | presence fetch failed → `unknown` | online + occupant dot |
| Peer | hidden until loaded | no peers → omit the outer arc entirely | unreachable → hollow dashed | turquoise |

Global: **offline / GPU-saturated** state (a first-class Renfield condition) must
render as a calm "system busy" core treatment, not an error — it's routine for a
shared-GPU household.

## Where it fits

1. **Phase 1 — admin page** `/admin/command-center`: the unified ops view. Pure
   composition of the six existing endpoints + the chat WS pulse. This is the
   high-value, low-risk slice; it replaces "open six tabs to understand the
   system" with one board.
2. **Phase 2 — live pulse + drill-downs** wired to the real WS stream and the
   admin pages.
3. **Phase 3 — household/kiosk ambient mode** (circle-aware, non-admin,
   content-free): a wall-tablet "what's the house doing" display. This is the
   Renfield-unique payoff — no survey chat UI has satellites/rooms to show — but
   it carries the authz work above and should follow only if the kiosk use case
   is real.

## Relationship to the chat-UI modernization roadmap

The Command Center is **not** a chat-UI roadmap item — it's an **ops/awareness**
surface, a sibling to the admin pages, not part of `/chat`. But it **reuses two
roadmap outputs directly**: role-surfacing (item 6, the `agent_role` on the wire)
for the live pulse, and the `presence_map` data (item 10) for the rooms ring. It
ships independently of the roadmap's open items.

## Open questions (resolve when scheduling Phase 1)

1. **Ring crowding.** 11+ MCP tools on one ring at 375px is unreadable. Mobile
   likely needs a collapsed/list fallback (the constellation is a
   desktop/kiosk-first layout). Define the responsive breakpoint behaviour.
2. **Role↔tool edges.** Apex draws every node to the core. Showing *which tools a
   role can use* (the `agent_roles.yaml` `mcp_tools`/`internal_tools` lists) as
   on-hover edges is richer but risks spaghetti — decide hover-only vs always-on.
3. **Pulse history.** Should the board show only the *current* turn, or a short
   decaying trail of the last N role activations (a heartbeat)? Trail is prettier
   but needs a small client-side ring buffer.
4. **Federation arc.** Include peers in v1 or defer? (Federation is itself
   relatively new; the data exists but the visual adds an outer ring.)
5. **Premise.** Same caveat as the chat roadmap: is an ops board worth building,
   or is the value really the **Phase 3 kiosk**? If the kiosk is the goal, design
   the circle-aware projection first so Phase 1 doesn't bake in admin-only
   assumptions.
```
