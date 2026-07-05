// Kiosk constellation — typed model for the live wall-display rings.
// `useKioskModel` assembles this shape from the `/ws/kiosk` push snapshot +
// deltas (content-free). Retained from the decommissioned admin Command Center;
// the type names kept their `…Node`/`CommandCenterModel` spelling to bound the
// diff — they now describe the kiosk's rings.

export type NodeHealth = 'healthy' | 'degraded' | 'down' | 'unknown';

export interface CoreNode {
  /** Display name of the orchestrator, e.g. "Renfield". */
  label: string;
  /** id of the agent role answering the current turn, if any (live pulse). */
  activeRoleId?: string;
}

export interface RoleNode {
  id: string;
  /** Human label, already localized by the caller (roles come from agent_roles.yaml). */
  label: string;
  /** MCP servers this role may reach; null/undefined = all (agent_roles.yaml
   *  semantics). Backs the hover/focus reach-edges to the tools ring. */
  reachServers?: string[] | null;
  /** Localized description for the tooltip. */
  hint?: string;
}

export interface ToolNode {
  id: string;
  label: string;
  health: NodeHealth;
  /** Tooltip detail, e.g. "12 tools" or the last error. */
  hint?: string;
  /** True for an INTERNAL-only subsystem (knowledge / presence / media) that has
   *  no MCP server: a pulse-only pseudo-node with no health, excluded from the
   *  tool-health telemetry counts and rendered distinctly (dotted, dim). */
  synthetic?: boolean;
}

/** The satellite voice states, colour-coded on the kiosk to match the physical
 *  LED ring (src/satellite/.../hardware/led.py). */
export type SatelliteState = 'idle' | 'listening' | 'processing' | 'speaking' | 'error';

export interface RoomNode {
  id: string;
  label: string;
  online: boolean;
  /** Number of people the presence service currently places in this room. */
  occupants: number;
  /** Most significant live state among the room's online satellites (kiosk
   *  colour-codes the dot by this, matching the satellite LED ring). Absent
   *  when the room has no satellite / is offline. */
  state?: SatelliteState;
  /** Tooltip detail, e.g. the satellite id or occupant names. */
  hint?: string;
}

export interface PeerNode {
  id: string;
  label: string;
  online: boolean;
}

/** One recent role activation (the pulse trail). Content-free by design:
 *  role id + timestamp + the turn's tool outcome, nothing else. */
export interface PulseEntry {
  roleId: string;
  /** Epoch milliseconds of the activation. */
  at: number;
  ok: boolean | null;
}

export interface CommandCenterModel {
  core: CoreNode;
  roles: RoleNode[];
  tools: ToolNode[];
  rooms: RoomNode[];
  /** Federation instances — optional; the outer arc is omitted when empty. */
  peers?: PeerNode[];
  /** Recent activations, newest first — drives the active edge + decay trail. */
  trail?: PulseEntry[];
}
