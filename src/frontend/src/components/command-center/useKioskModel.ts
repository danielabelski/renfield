// Kiosk model = the live CommandCenterModel + a voice-reactive core state
// derived from the satellites' own state (idle/listening/processing/speaking).
// The wall display reacts to real household voice activity, not a simulation.
//
// DATA SOURCE (phase 1b): this now reads from the PUSH socket useKioskSocket()
// instead of the former react-query polls (useCommandCenterModel +
// useSatellites/Weather/NowPlaying queries). The derivation math below is
// unchanged — voice-state priority merge + telemetry counts are byte-for-byte
// the same, and the ring assembly (roles / tools / rooms / peers) mirrors the
// admin board's useCommandCenterModel exactly, only re-sourced from the
// snapshot+delta reducer. useCommandCenterModel stays in place for the
// (still-live) admin Command Center; the two converge when it is decommissioned
// (tasks/kiosk-active-subsystem-plan.md §3).
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { roleLabel } from '../chat/AgentRoleBadge';
import { useKioskSocket, type KioskLiveModel } from './useKioskSocket';
import type { KioskWeather, KioskNowPlaying } from '../../api/resources/commandCenter';
import type { CommandCenterModel, NodeHealth } from './types';

export type CoreState = 'idle' | 'listening' | 'processing' | 'speaking' | 'busy';

/** Voice states in ascending priority — a speaking satellite wins over a
 *  merely-listening one when several are active at once. ('error' is handled
 *  separately, not here, so a fleet error can't read as idle.) */
const STATE_PRIORITY: Record<string, number> = {
  idle: 0,
  listening: 1,
  processing: 2,
  speaking: 3,
};

/** A satellite whose last heartbeat is older than this is treated as offline —
 *  its self-reported state (e.g. a stale 'listening') must NOT drive the core.
 *  Mirrors the same window useCommandCenterModel applies. */
const SATELLITE_OFFLINE_S = 90;

// ---- ring-assembly constants (mirrored from useCommandCenterModel) ---------
/** An activation older than this no longer lights the core (the turn is over). */
const ACTIVE_WINDOW_MS = 90_000;
/** Trail entries older than this are fully decayed and dropped from the board. */
const TRAIL_WINDOW_MS = 15 * 60_000;
/** Wall-clock recompute cadence: active-role expiry / trail decay / peer
 *  reachability must advance by the PASSAGE OF TIME, not only on new data. */
const CLOCK_TICK_MS = 15_000;
/** A federation peer unseen for longer than this renders unreachable. */
const PEER_OFFLINE_MS = 10 * 60_000;
/** Aggregated per-server success rate below this (with enough calls) = degraded. */
const DEGRADED_SUCCESS_RATE = 0.8;
const DEGRADED_MIN_CALLS = 3;

/** Display names for MCP servers whose ids don't title-case cleanly. */
const SERVER_LABELS: Record<string, string> = {
  homeassistant: 'Home Assistant',
  dlna: 'DLNA',
  n8n: 'n8n',
  searxng: 'SearXNG',
  tts: 'TTS',
};

function prettifyServerName(name: string): string {
  const known = SERVER_LABELS[name.toLowerCase()];
  if (known) return known;
  return name
    .split(/[_-]/)
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ');
}

/** The backend emits naive-UTC ISO strings (no zone suffix); anchor them so
 *  Date.parse doesn't read them as LOCAL time. */
function parseNaiveUtcMs(iso: string): number {
  return Date.parse(iso.endsWith('Z') ? iso : `${iso}Z`);
}

/** Stable empty default so `?? EMPTY` doesn't mint a fresh array reference each
 *  render (which would defeat the useMemo below). */
const EMPTY_NOW_PLAYING: KioskNowPlaying[] = [];

export interface KioskState {
  model: CommandCenterModel;
  bootLoading: boolean;
  backendUnreachable: boolean;
  /** What the glowing core shows right now. */
  core: CoreState;
  /** Room where the active voice interaction is happening (if any). */
  activeRoom: string | null;
  /** Localized label of the agent role answering this turn (if any). */
  activeRoleLabel: string | null;
  /** Home-location weather for the ambient tile (null = hide the tile). */
  weather: KioskWeather | null;
  /** Live media-follow sessions for the now-playing tile (empty = hide). */
  nowPlaying: KioskNowPlaying[];
  /** subsystem id → epoch-ms last active. Drives the active-subsystem pulse in
   *  KioskConstellation (a MCP-server node glows when its subsystem is named by
   *  a `turn_activity` event). The view fades it on its own render tick. */
  subsystemPulses: Record<string, number>;
  /** At-a-glance counts for the ambient telemetry corner. */
  telemetry: {
    satellitesOnline: number;
    satellitesTotal: number;
    peoplePresent: number;
    occupiedRooms: number;
    toolsHealthy: number;
    toolsTotal: number;
  };
}

/** Assemble the ring model (roles / tools / rooms / peers / trail / activeRole)
 *  from the pushed live model. Mirrors useCommandCenterModel's derivation,
 *  adapted to the snapshot section shapes. */
function buildCommandCenterModel(
  live: KioskLiveModel,
  t: ReturnType<typeof useTranslation>['t'],
  lang: 'de' | 'en',
  now: number,
): CommandCenterModel {
  // ---- roles ring -------------------------------------------------------
  const roles = live.roles.map((role) => ({
    id: role.name,
    label: roleLabel(t, role.name),
    reachServers: role.mcp_servers,
    hint: role.description?.[lang] ?? role.description?.en,
  }));

  // ---- pulse trail ------------------------------------------------------
  const trail = live.activity
    .map((entry) => ({
      roleId: entry.role,
      at: parseNaiveUtcMs(entry.at),
      ok: entry.ok,
    }))
    .filter((entry) => Number.isFinite(entry.at) && now - entry.at < TRAIL_WINDOW_MS);
  const head = trail[0];
  const activeRoleId =
    head && now - head.at < ACTIVE_WINDOW_MS ? head.roleId : undefined;

  // ---- tools ring -------------------------------------------------------
  // Health = MCP connection state, downgraded when a connected server's tools
  // are failing. The snapshot pre-classifies per tool (total + success_rate);
  // reconstruct per-tool succ/fail and aggregate per server so the SAME 0.8 /
  // min-3 threshold the admin board uses still decides "degraded".
  const failing = new Map<string, { succ: number; fail: number }>();
  for (const stat of live.toolHealth) {
    const match = /^mcp\.([^.]+)\./.exec(stat.tool_name);
    if (!match) continue;
    const succ = Math.round(stat.total * stat.success_rate);
    const fail = stat.total - succ;
    const agg = failing.get(match[1]) ?? { succ: 0, fail: 0 };
    agg.succ += succ;
    agg.fail += fail;
    failing.set(match[1], agg);
  }
  const tools = live.mcp.servers.map((server) => {
    let health: NodeHealth;
    if (!server.connected) {
      health = 'down';
    } else if (server.last_error) {
      health = 'degraded';
    } else {
      const agg = failing.get(server.name);
      const total = agg ? agg.succ + agg.fail : 0;
      health =
        agg && total >= DEGRADED_MIN_CALLS &&
        agg.succ / total < DEGRADED_SUCCESS_RATE
          ? 'degraded'
          : 'healthy';
    }
    return {
      id: server.name,
      label: prettifyServerName(server.name),
      health,
      hint: server.connected
        ? t('commandCenter.toolHint', {
            count: server.tool_count,
            defaultValue: '{{count}} tools',
          })
        : server.last_error || t('commandCenter.legend.down'),
    };
  });

  // ---- rooms ring -------------------------------------------------------
  // Union of satellite rooms (online state) and presence rooms (occupants).
  const occupantsByRoom = new Map<string, number>();
  for (const room of live.presence.rooms) {
    if (!room.room_name) continue;
    occupantsByRoom.set(room.room_name.toLowerCase(), room.occupants);
  }
  const STATE_RANK: Record<string, number> = {
    idle: 0, listening: 1, processing: 2, speaking: 3, error: 4,
  };
  const rooms = new Map<
    string,
    {
      id: string; label: string; online: boolean; occupants: number;
      state?: 'idle' | 'listening' | 'processing' | 'speaking' | 'error';
      hint?: string;
    }
  >();
  for (const sat of live.satellites) {
    const key = sat.room.toLowerCase();
    const online = sat.heartbeat_ago_seconds < SATELLITE_OFFLINE_S;
    const existing = rooms.get(key);
    let state = existing?.state;
    if (online) {
      if (!state || (STATE_RANK[sat.state] ?? 0) > (STATE_RANK[state] ?? 0)) {
        state = sat.state;
      }
    }
    rooms.set(key, {
      id: key,
      label: sat.room,
      online: (existing?.online ?? false) || online,
      occupants: occupantsByRoom.get(key) ?? 0,
      state,
      hint: sat.satellite_id,
    });
  }
  for (const [key, occupants] of occupantsByRoom) {
    if (rooms.has(key)) continue;
    const label = live.presence.rooms.find(
      (room) => room.room_name?.toLowerCase() === key,
    )?.room_name;
    rooms.set(key, {
      id: key,
      label: label ?? key,
      online: true, // presence saw someone here; there's just no satellite
      occupants,
      hint: t('commandCenter.noSatellite', { defaultValue: 'No satellite' }),
    });
  }

  // ---- peers arc --------------------------------------------------------
  const peers = live.peers.map((peer) => {
    const lastSeen = peer.last_seen_at ? parseNaiveUtcMs(peer.last_seen_at) : NaN;
    return {
      id: String(peer.id),
      label: peer.name,
      online: Number.isFinite(lastSeen) && now - lastSeen < PEER_OFFLINE_MS,
    };
  });

  return {
    core: { label: 'Renfield', activeRoleId },
    roles,
    tools,
    rooms: [...rooms.values()].sort((a, b) => a.label.localeCompare(b.label)),
    peers,
    trail,
  };
}

export function useKioskModel(): KioskState {
  const { t, i18n } = useTranslation();
  const { live, bootLoading, reconnecting } = useKioskSocket();
  const lang = i18n.language?.startsWith('de') ? 'de' : 'en';

  // Wall-clock input for the memo: active-role expiry / trail decay / peer
  // reachability must advance even when no new event arrives.
  const [nowTick, setNowTick] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(id);
  }, []);

  const weather = live.weather;
  const nowPlaying = live.nowPlaying.length > 0 ? live.nowPlaying : EMPTY_NOW_PLAYING;
  const subsystemPulses = live.subsystemPulses;

  // A dropped socket after the first hydrate = the board is now stale; surface
  // it exactly like the old all-queries-failed state (calm "reconnecting" +
  // busy core), NOT a frozen board read as live.
  const backendUnreachable = !bootLoading && reconnecting;

  return useMemo<KioskState>(() => {
    const model = buildCommandCenterModel(live, t, lang, nowTick);

    // Only satellites with a fresh heartbeat count as live — a dead one can't
    // be "listening", however it last reported.
    const onlineSats = live.satellites.filter(
      (s) => s.heartbeat_ago_seconds < SATELLITE_OFFLINE_S,
    );

    // ---- voice-reactive core state from the ONLINE satellites' own state ---
    let core: CoreState = backendUnreachable ? 'busy' : 'idle';
    let activeRoom: string | null = null;
    let best = 0;
    let anyError = false;
    for (const sat of onlineSats) {
      if (sat.state === 'error') { anyError = true; continue; }
      const p = STATE_PRIORITY[sat.state] ?? 0;
      if (p > best) {
        best = p;
        core = sat.state as CoreState;
        activeRoom = sat.room;
      }
    }
    if (!backendUnreachable && best === 0 && anyError) core = 'busy';

    const activeRoleLabel = model.core.activeRoleId
      ? roleLabel(t, model.core.activeRoleId)
      : null;

    // People + occupied rooms from the SAME online-filtered set so the two
    // telemetry numbers can never disagree ("2 in 0 rooms").
    const liveOccupiedRooms = model.rooms.filter((r) => r.online && r.occupants > 0);

    return {
      model,
      bootLoading,
      backendUnreachable,
      core,
      activeRoom,
      activeRoleLabel,
      weather,
      nowPlaying,
      subsystemPulses,
      telemetry: {
        satellitesOnline: onlineSats.length,
        satellitesTotal: live.satellites.length,
        peoplePresent: liveOccupiedRooms.reduce((n, r) => n + r.occupants, 0),
        occupiedRooms: liveOccupiedRooms.length,
        toolsHealthy: model.tools.filter((tool) => tool.health === 'healthy').length,
        toolsTotal: model.tools.length,
      },
    };
  }, [t, lang, nowTick, live, bootLoading, backendUnreachable, weather, nowPlaying, subsystemPulses]);
}
