// Command Center — typed model for the live constellation.
// See docs/design/command-center.md. The component is fed this shape;
// useCommandCenterModel assembles it live from the admin endpoints + the
// content-free activity pulse (/api/command-center/activity).

export type NodeHealth = 'healthy' | 'degraded' | 'down' | 'unknown';

/** Per-ring fetch state so each ring can render its own loading/error
 *  treatment without blanking the whole board. */
export type RingStatus = 'loading' | 'ready' | 'error';

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
}

export interface RoomNode {
  id: string;
  label: string;
  online: boolean;
  /** Number of people the presence service currently places in this room. */
  occupants: number;
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
  /** Per-ring fetch state (absent = ready, keeps the demo model terse). */
  ringStatus?: Partial<Record<'roles' | 'tools' | 'rooms' | 'peers', RingStatus>>;
}
