// Live CommandCenterModel assembly (Phase 1 of docs/design/command-center.md).
//
// Pure composition over endpoints that already exist — plus the two new
// read-only command-center feeds (agent roles + the content-free activity
// pulse). Each ring carries its own RingStatus so a single failing source
// degrades ONE ring to its error treatment instead of blanking the board.
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { roleLabel } from '../chat/AgentRoleBadge';
import {
  useAgentRolesQuery,
  useRoleActivityQuery,
} from '../../api/resources/commandCenter';
import { useMcpStatusQuery } from '../../api/resources/integrations';
import { useToolStatsQuery } from '../../api/resources/toolHealth';
import { useSatellitesQuery } from '../../api/resources/satellites';
import { usePresenceRoomsQuery } from '../../api/resources/presence';
import { useFederationPeersQuery } from '../../api/resources/federation';
import type {
  CommandCenterModel,
  NodeHealth,
  PulseEntry,
  RingStatus,
} from './types';

/** An activation older than this no longer lights the core (the turn is over). */
const ACTIVE_WINDOW_MS = 90_000;
/** Trail entries older than this are fully decayed and dropped from the board. */
export const TRAIL_WINDOW_MS = 15 * 60_000;
/** A satellite whose last heartbeat is older than this renders offline. */
const SATELLITE_OFFLINE_S = 90;
/** A federation peer unseen for longer than this renders unreachable. */
const PEER_OFFLINE_MS = 10 * 60_000;
/** Aggregated per-server success rate below this (with enough calls) = degraded. */
const DEGRADED_SUCCESS_RATE = 0.8;
const DEGRADED_MIN_CALLS = 3;

interface QueryLike {
  isLoading: boolean;
  isError: boolean;
}

function ringStatus(...queries: QueryLike[]): RingStatus {
  // A ring fed by several queries is "ready" once its primary data can render;
  // loading wins over error so first paint shows skeletons, not alarms.
  if (queries.some((q) => q.isLoading)) return 'loading';
  if (queries.every((q) => q.isError)) return 'error';
  return 'ready';
}

/** Display names for MCP servers whose ids don't title-case cleanly. */
const SERVER_LABELS: Record<string, string> = {
  homeassistant: 'Home Assistant',
  dlna: 'DLNA',
  n8n: 'n8n',
  searxng: 'SearXNG',
  tts: 'TTS',
};

/** "paperless" → "Paperless", "home_assistant" → "Home Assistant". */
function prettifyServerName(name: string): string {
  const known = SERVER_LABELS[name.toLowerCase()];
  if (known) return known;
  return name
    .split(/[_-]/)
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ');
}

export interface CommandCenterState {
  model: CommandCenterModel;
  /** True while nothing has loaded yet (first paint skeleton). */
  bootLoading: boolean;
  /** True when every source failed — the calm "backend unreachable" state. */
  backendUnreachable: boolean;
  /** Newest-first, decayed to the trail window — feeds the activity rail. */
  trail: PulseEntry[];
  refetchAll: () => void;
}

export function useCommandCenterModel(): CommandCenterState {
  const { t, i18n } = useTranslation();

  const rolesQuery = useAgentRolesQuery();
  const activityQuery = useRoleActivityQuery();
  const mcpQuery = useMcpStatusQuery();
  const toolStatsQuery = useToolStatsQuery(null);
  const satellitesQuery = useSatellitesQuery(true);
  const presenceQuery = usePresenceRoomsQuery(true);
  const peersQuery = useFederationPeersQuery();

  const lang = i18n.language?.startsWith('de') ? 'de' : 'en';

  // Destructured so the memo depends on stable data references, not on the
  // per-render query result objects.
  const rolesData = rolesQuery.data;
  const activityData = activityQuery.data;
  const mcpData = mcpQuery.data;
  const toolStatsData = toolStatsQuery.data;
  const satellitesData = satellitesQuery.data;
  const presenceData = presenceQuery.data;
  const peersData = peersQuery.data;
  const rolesRing = ringStatus(rolesQuery);
  const toolsRing = ringStatus(mcpQuery, toolStatsQuery);
  const roomsRing = ringStatus(satellitesQuery, presenceQuery);
  const peersRing = ringStatus(peersQuery);

  const model = useMemo<CommandCenterModel>(() => {
    // ---- roles ring -------------------------------------------------------
    const roles = (rolesData ?? []).map((role) => ({
      id: role.name,
      label: roleLabel(t, role.name),
      reachServers: role.mcp_servers,
      hint: role.description?.[lang] ?? role.description?.en,
    }));

    // ---- pulse trail ------------------------------------------------------
    const now = Date.now();
    const trail: PulseEntry[] = (activityData ?? [])
      .map((entry) => ({
        roleId: entry.role,
        // The API returns naive-UTC timestamps (no zone suffix); anchor them.
        at: Date.parse(entry.at.endsWith('Z') ? entry.at : `${entry.at}Z`),
        ok: entry.ok,
      }))
      .filter((entry) => Number.isFinite(entry.at) && now - entry.at < TRAIL_WINDOW_MS);
    const head = trail[0];
    const activeRoleId =
      head && now - head.at < ACTIVE_WINDOW_MS ? head.roleId : undefined;

    // ---- tools ring -------------------------------------------------------
    // Health = MCP connection state, downgraded by the outcome tracker when a
    // connected server's tools are failing (aggregate per server prefix).
    const failing = new Map<string, { succ: number; fail: number }>();
    for (const stat of toolStatsData ?? []) {
      const match = /^mcp\.([^.]+)\./.exec(stat.tool_name);
      if (!match) continue;
      const agg = failing.get(match[1]) ?? { succ: 0, fail: 0 };
      agg.succ += stat.success_count;
      agg.fail += stat.failure_count;
      failing.set(match[1], agg);
    }
    const tools = (mcpData?.servers ?? []).map((server) => {
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
    // Union of satellite rooms (online state) and presence rooms (occupants):
    // a room can have people without a satellite and vice versa.
    const occupantsByRoom = new Map<string, number>();
    for (const room of presenceData ?? []) {
      if (!room.room_name) continue;
      occupantsByRoom.set(room.room_name.toLowerCase(), room.occupants.length);
    }
    const rooms = new Map<
      string,
      { id: string; label: string; online: boolean; occupants: number; hint?: string }
    >();
    for (const sat of satellitesData?.satellites ?? []) {
      const key = sat.room.toLowerCase();
      const online = sat.heartbeat_ago_seconds < SATELLITE_OFFLINE_S;
      const existing = rooms.get(key);
      rooms.set(key, {
        id: key,
        label: sat.room,
        // Several satellites can share a room; one alive keeps the room online.
        online: (existing?.online ?? false) || online,
        occupants: occupantsByRoom.get(key) ?? 0,
        hint: sat.satellite_id,
      });
    }
    for (const [key, occupants] of occupantsByRoom) {
      if (rooms.has(key)) continue;
      const label = (presenceData ?? []).find(
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
    const peers = (peersData ?? []).map((peer) => {
      const lastSeen = peer.last_seen_at ? Date.parse(peer.last_seen_at) : NaN;
      return {
        id: String(peer.id),
        label: peer.remote_display_name,
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
      ringStatus: {
        roles: rolesRing,
        tools: toolsRing,
        rooms: roomsRing,
        peers: peersRing,
      },
    };
  }, [
    t,
    lang,
    rolesData,
    activityData,
    mcpData,
    toolStatsData,
    satellitesData,
    presenceData,
    peersData,
    rolesRing,
    toolsRing,
    roomsRing,
    peersRing,
  ]);

  const allQueries = [
    rolesQuery,
    activityQuery,
    mcpQuery,
    toolStatsQuery,
    satellitesQuery,
    presenceQuery,
    peersQuery,
  ];
  // useMcpStatusQuery swallows fetch errors (resolves to an empty status), so
  // it can never report isError — judge reachability by the queries that can.
  const errorCapable = allQueries.filter((q) => q !== mcpQuery);

  return {
    model,
    bootLoading: allQueries.every((q) => q.isLoading),
    backendUnreachable: errorCapable.every((q) => q.isError),
    trail: model.trail ?? [],
    refetchAll: () => allQueries.forEach((q) => void q.refetch()),
  };
}
