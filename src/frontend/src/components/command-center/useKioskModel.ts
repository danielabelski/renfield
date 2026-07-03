// Kiosk model = the live CommandCenterModel + a voice-reactive core state
// derived from the satellites' own state (idle/listening/processing/speaking).
// The wall display reacts to real household voice activity, not a simulation.
import { useMemo } from 'react';

import { useCommandCenterModel } from './useCommandCenterModel';
import { useSatellitesQuery } from '../../api/resources/satellites';
import {
  useKioskWeatherQuery,
  useKioskNowPlayingQuery,
  type KioskWeather,
  type KioskNowPlaying,
} from '../../api/resources/commandCenter';
import { roleLabel } from '../chat/AgentRoleBadge';
import type { CommandCenterModel } from './types';
import { useTranslation } from 'react-i18next';

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

export function useKioskModel(): KioskState {
  const { t } = useTranslation();
  const { model, bootLoading, backendUnreachable } = useCommandCenterModel();
  const satellitesQuery = useSatellitesQuery(true);
  const sats = satellitesQuery.data?.satellites;
  const weather = useKioskWeatherQuery().data ?? null;
  const nowPlaying = useKioskNowPlayingQuery().data ?? EMPTY_NOW_PLAYING;

  return useMemo<KioskState>(() => {
    const satList = sats ?? [];
    // Only satellites with a fresh heartbeat count as live — a dead one can't
    // be "listening", however it last reported.
    const onlineSats = satList.filter((s) => s.heartbeat_ago_seconds < SATELLITE_OFFLINE_S);

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
    // Nothing actively speaking/listening but a live satellite is erroring →
    // surface it as a busy/alert core, not a false "ready".
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
      telemetry: {
        // From the real satellite list, not the room union (rooms dedupe
        // multiple satellites and include presence-only rooms).
        satellitesOnline: onlineSats.length,
        satellitesTotal: satList.length,
        peoplePresent: liveOccupiedRooms.reduce((n, r) => n + r.occupants, 0),
        occupiedRooms: liveOccupiedRooms.length,
        toolsHealthy: model.tools.filter((tool) => tool.health === 'healthy').length,
        toolsTotal: model.tools.length,
      },
    };
  }, [t, model, bootLoading, backendUnreachable, sats, weather, nowPlaying]);
}
