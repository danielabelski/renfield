// useKioskSocket — the PUSH data source for the /kiosk wall display.
//
// Opens the ADMIN-gated `/ws/kiosk` hub (backend api/websocket/kiosk_handler.py,
// merged phase 1a), hydrates from the one `snapshot` message it sends on
// connect, then folds each delta event into a single reducer-held
// `KioskLiveModel`. This replaces the kiosk's former react-query POLLING chain
// (useCommandCenterModel + useSatellites/Weather/NowPlaying queries) with a
// single event-driven socket — no browser timers hit our own REST API.
//
// PHASE-1 INTERIM (documented, approved incremental plan — tasks/
// kiosk-active-subsystem-plan.md §7 step 4): the backend currently emits only
// two live deltas — `satellite_state` and `turn_activity`. So satellite voice
// state and the active-subsystem pulse are LIVE, but presence / weather /
// now-playing / tool-health / peers are SNAPSHOT-ONLY: correct on every
// connect/reconnect, but not live-updated until phase 2 wires their deltas.
// The reducer already ignores unknown event `type`s gracefully, so those
// future deltas land without a frontend change.
import { useEffect, useReducer, useRef, useState } from 'react';

import { debug } from '../../utils/debug';
import { getWebSocketUrl } from '../../utils/env';
import type { SatelliteState } from './types';
import type {
  AgentRoleInfo,
  KioskNowPlaying,
  KioskWeather,
  RoleActivityEntry,
} from '../../api/resources/commandCenter';

// ---- snapshot section shapes (as the backend hub emits them) --------------

export interface KioskSatellite {
  satellite_id: string;
  room: string;
  room_id: number | null;
  state: SatelliteState;
  /** Unix seconds of the last heartbeat (absent on a delta-inserted satellite). */
  last_heartbeat?: number;
  /** Seconds since the last heartbeat, captured at hydration. Frozen between
   *  snapshots — phase 1 has no heartbeat delta, so a still-connected satellite
   *  keeps its fresh value rather than decaying to a false "offline" (a fresh
   *  snapshot on reconnect re-anchors it). */
  heartbeat_ago_seconds: number;
  has_active_session?: boolean;
}

export interface KioskPresenceRoom {
  room_id: number;
  room_name: string | null;
  /** Occupant COUNT (content-free — no user ids). */
  occupants: number;
}

export interface KioskPresence {
  rooms: KioskPresenceRoom[];
  people_present: number;
  occupied_rooms: number;
}

export interface KioskMcpServer {
  name: string;
  connected: boolean;
  last_error?: string | null;
  tool_count: number;
}

export interface KioskMcp {
  enabled: boolean;
  total_tools: number;
  servers: KioskMcpServer[];
}

export interface KioskToolHealth {
  tool_name: string;
  total: number;
  success_rate: number;
  degraded: boolean;
}

export interface KioskPeer {
  id: string;
  name: string;
  last_seen_at: string | null;
  reachable: boolean;
}

/** The full folded model the kiosk derives its view from. */
export interface KioskLiveModel {
  /** False until the first `snapshot` arrives (first-paint skeleton gate). */
  hydrated: boolean;
  /** ISO timestamp of the most recent snapshot. */
  at: string | null;
  satellites: KioskSatellite[];
  presence: KioskPresence;
  mcp: KioskMcp;
  toolHealth: KioskToolHealth[];
  roles: AgentRoleInfo[];
  activity: RoleActivityEntry[];
  peers: KioskPeer[];
  weather: KioskWeather | null;
  nowPlaying: KioskNowPlaying[];
  /** subsystem id → epoch-ms of its most recent `turn_activity` mention. Drives
   *  the active-subsystem pulse; the view fades it on a render tick. Preserved
   *  across snapshots (it is delta-sourced state with its own decay). */
  subsystemPulses: Record<string, number>;
}

// ---- wire message shapes ---------------------------------------------------

interface SnapshotMessage {
  type: 'snapshot';
  at?: string;
  satellites?: Array<{
    satellite_id: string;
    room: string;
    room_id?: number | null;
    state: SatelliteState;
    last_heartbeat?: number;
    has_active_session?: boolean;
  }>;
  presence?: {
    rooms?: KioskPresenceRoom[];
    people_present?: number;
    occupied_rooms?: number;
  };
  mcp?: KioskMcp;
  tool_health?: KioskToolHealth[];
  roles?: AgentRoleInfo[];
  activity?: RoleActivityEntry[];
  peers?: KioskPeer[];
  weather?: KioskWeather | null;
  now_playing?: KioskNowPlaying[];
}

interface SatelliteStateDelta {
  type: 'satellite_state';
  satellite_id: string;
  room: string;
  room_id?: number | null;
  state: SatelliteState;
}

interface TurnActivityDelta {
  type: 'turn_activity';
  role: string;
  subsystems?: string[];
  ok?: boolean | null;
  at?: string;
}

/** Any parsed inbound frame. Unknown `type`s are tolerated (see reducer). */
type KioskMessage =
  | SnapshotMessage
  | SatelliteStateDelta
  | TurnActivityDelta
  | { type: string; [key: string]: unknown };

// ---- reducer ---------------------------------------------------------------

const EMPTY_MODEL: KioskLiveModel = {
  hydrated: false,
  at: null,
  satellites: [],
  presence: { rooms: [], people_present: 0, occupied_rooms: 0 },
  mcp: { enabled: false, total_tools: 0, servers: [] },
  toolHealth: [],
  roles: [],
  activity: [],
  peers: [],
  weather: null,
  nowPlaying: [],
  subsystemPulses: {},
};

/** Recent role activations kept for the pulse trail — bounded so a long-lived
 *  tab's activity list can't grow without limit off `turn_activity` deltas. */
const ACTIVITY_CAP = 30;

/** Naive-UTC-safe parse: the backend emits ISO strings that may lack a zone
 *  suffix; anchor them to UTC so Date.parse doesn't read them as local time. */
function parseAtMs(iso: string | undefined): number {
  if (!iso) return Date.now();
  const ms = Date.parse(iso.endsWith('Z') ? iso : `${iso}Z`);
  return Number.isFinite(ms) ? ms : Date.now();
}

function hydrate(prev: KioskLiveModel, msg: SnapshotMessage): KioskLiveModel {
  const nowSec = Date.now() / 1000;
  return {
    hydrated: true,
    at: msg.at ?? null,
    satellites: (msg.satellites ?? []).map((s) => ({
      satellite_id: s.satellite_id,
      room: s.room,
      room_id: s.room_id ?? null,
      state: s.state,
      last_heartbeat: s.last_heartbeat,
      heartbeat_ago_seconds:
        typeof s.last_heartbeat === 'number' ? Math.max(0, nowSec - s.last_heartbeat) : 0,
      has_active_session: s.has_active_session,
    })),
    presence: {
      rooms: msg.presence?.rooms ?? [],
      people_present: msg.presence?.people_present ?? 0,
      occupied_rooms: msg.presence?.occupied_rooms ?? 0,
    },
    mcp: msg.mcp ?? { enabled: false, total_tools: 0, servers: [] },
    toolHealth: msg.tool_health ?? [],
    roles: msg.roles ?? [],
    activity: msg.activity ?? [],
    peers: msg.peers ?? [],
    weather: msg.weather ?? null,
    nowPlaying: msg.now_playing ?? [],
    // Preserve the pulse map: it is delta-sourced and self-decays in the view,
    // so a reconnect snapshot must not wipe an in-flight subsystem pulse.
    subsystemPulses: prev.subsystemPulses,
  };
}

function reduce(state: KioskLiveModel, msg: KioskMessage): KioskLiveModel {
  switch (msg.type) {
    case 'snapshot':
      return hydrate(state, msg as SnapshotMessage);

    case 'satellite_state': {
      const delta = msg as SatelliteStateDelta;
      let found = false;
      const satellites = state.satellites.map((sat) => {
        if (sat.satellite_id !== delta.satellite_id) return sat;
        found = true;
        return {
          ...sat,
          room: delta.room ?? sat.room,
          room_id: delta.room_id ?? sat.room_id,
          state: delta.state,
        };
      });
      if (!found) {
        // A state transition for a satellite not in the last snapshot (e.g. it
        // connected after hydrate). Treat it as freshly live.
        satellites.push({
          satellite_id: delta.satellite_id,
          room: delta.room,
          room_id: delta.room_id ?? null,
          state: delta.state,
          heartbeat_ago_seconds: 0,
        });
      }
      return { ...state, satellites };
    }

    case 'turn_activity': {
      const delta = msg as TurnActivityDelta;
      const at = delta.at ?? new Date().toISOString();
      const atMs = parseAtMs(delta.at);
      const activity = [
        { role: delta.role, at, ok: delta.ok ?? null },
        ...state.activity,
      ].slice(0, ACTIVITY_CAP);
      const subsystemPulses = { ...state.subsystemPulses };
      for (const sub of delta.subsystems ?? []) {
        if (typeof sub === 'string' && sub) subsystemPulses[sub] = atMs;
      }
      return { ...state, activity, subsystemPulses };
    }

    default:
      // Unknown event type — tolerate gracefully so a later delta phase can
      // ship on the backend without breaking an already-deployed kiosk tab.
      return state;
  }
}

// ---- connection lifecycle --------------------------------------------------

export type KioskConnStatus = 'connecting' | 'open' | 'reconnecting';

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;

export interface KioskSocketState {
  live: KioskLiveModel;
  status: KioskConnStatus;
  /** True until the first snapshot lands (first-paint skeleton). */
  bootLoading: boolean;
  /** True whenever the socket is not currently open (connecting or dropped). */
  reconnecting: boolean;
}

function kioskWsUrl(): string {
  // getWebSocketUrl() returns `.../ws`; strip it and append our own path —
  // the canonical pattern (useDeviceConnection.getWsUrl for `/ws/device`).
  let url = getWebSocketUrl().replace(/\/ws$/, '') + '/ws/kiosk';
  // Same JWT-in-query auth the chat + KG-live sockets use; the hub verifies it
  // and requires Permission.ADMIN at connect.
  const token = localStorage.getItem('renfield_access_token');
  if (token) url += `?token=${token}`;
  return url;
}

/**
 * Subscribe to the live kiosk hub. Returns the folded model plus a coarse
 * connection status the kiosk surfaces (so a dropped socket shows a calm
 * "reconnecting" state instead of silently freezing on stale data).
 *
 * Reconnect uses exponential backoff (1s → 30s, reset on open). On every
 * (re)connect the hub re-sends a full `snapshot`, so a missed delta during a
 * blip self-heals without the client asking for anything.
 */
export function useKioskSocket(): KioskSocketState {
  const [live, dispatch] = useReducer(reduce, EMPTY_MODEL);
  const [status, setStatus] = useState<KioskConnStatus>('connecting');

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const intentionalCloseRef = useRef(false);

  useEffect(() => {
    intentionalCloseRef.current = false;

    const connect = () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }

      let ws: WebSocket;
      try {
        ws = new WebSocket(kioskWsUrl());
      } catch (err) {
        debug.log('Kiosk WS construct failed:', err);
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        debug.log('Kiosk WS connected');
        attemptRef.current = 0;
        setStatus('open');
      };

      ws.onmessage = (event: MessageEvent) => {
        let msg: KioskMessage;
        try {
          msg = JSON.parse(event.data as string) as KioskMessage;
        } catch {
          return; // ignore malformed frames rather than crash the tab
        }
        dispatch(msg);
      };

      ws.onerror = (err: Event) => {
        debug.log('Kiosk WS error:', err);
        // Let onclose drive the reconnect (browsers fire error → close).
      };

      ws.onclose = () => {
        if (intentionalCloseRef.current) return;
        debug.log('Kiosk WS closed — scheduling reconnect');
        setStatus('reconnecting');
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      const delay = Math.min(
        MAX_BACKOFF_MS,
        INITIAL_BACKOFF_MS * 2 ** attemptRef.current,
      );
      attemptRef.current += 1;
      reconnectTimerRef.current = setTimeout(connect, delay);
    };

    connect();

    return () => {
      intentionalCloseRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, []);

  return {
    live,
    status,
    bootLoading: !live.hydrated,
    reconnecting: status !== 'open',
  };
}
