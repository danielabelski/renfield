/**
 * Kiosk wire types — the content-free shapes the `/kiosk` wall display consumes
 * from the `/ws/kiosk` push hub (snapshot + deltas; see components/kiosk/
 * useKioskSocket.ts). These moved here from the decommissioned `commandCenter.ts`
 * resource: only the type shapes survived — the react-query poll hooks were
 * command-center-only and were removed with it.
 */

export interface AgentRoleInfo {
  name: string;
  description: Record<string, string>;
  mcp_servers: string[] | null;
  internal_tools: string[] | null;
  has_agent_loop: boolean;
}

export interface RoleActivityEntry {
  role: string;
  /** ISO timestamp of the turn's assistant message. */
  at: string;
  /** action_success of the turn; null when the turn ran no tool. */
  ok: boolean | null;
}

export interface KioskWeather {
  location: string;
  temp: number;
  unit: string;
  /** WMO weather code → icon on the tile. */
  code: number;
  condition: string;
  high: number | null;
  low: number | null;
}

export interface KioskNowPlaying {
  room: string;
  kind: string;
  title: string;
  subtitle: string | null;
  track: number | null;
  total: number | null;
}
