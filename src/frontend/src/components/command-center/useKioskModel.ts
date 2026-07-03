// Kiosk model = the live CommandCenterModel + a voice-reactive core state
// derived from the satellites' own state (idle/listening/processing/speaking).
// The wall display reacts to real household voice activity, not a simulation.
import { useMemo } from 'react';

import { useCommandCenterModel } from './useCommandCenterModel';
import { useSatellitesQuery } from '../../api/resources/satellites';
import { roleLabel } from '../chat/AgentRoleBadge';
import type { CommandCenterModel } from './types';
import { useTranslation } from 'react-i18next';

export type CoreState = 'idle' | 'listening' | 'processing' | 'speaking' | 'busy';

/** Voice states in ascending priority — a speaking satellite wins over a
 *  merely-listening one when several are active at once. */
const STATE_PRIORITY: Record<string, number> = {
  idle: 0,
  error: 0,
  listening: 1,
  processing: 2,
  speaking: 3,
};

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

  return useMemo<KioskState>(() => {
    // ---- voice-reactive core state from the satellites' own state ---------
    let core: CoreState = backendUnreachable ? 'busy' : 'idle';
    let activeRoom: string | null = null;
    let best = 0;
    for (const sat of sats ?? []) {
      const p = STATE_PRIORITY[sat.state] ?? 0;
      if (p > best) {
        best = p;
        core = sat.state as CoreState;
        activeRoom = sat.room;
      }
    }

    const activeRoleLabel = model.core.activeRoleId
      ? roleLabel(t, model.core.activeRoleId)
      : null;

    const peoplePresent = model.rooms.reduce((n, r) => n + r.occupants, 0);
    const occupiedRooms = model.rooms.filter((r) => r.online && r.occupants > 0).length;

    return {
      model,
      bootLoading,
      backendUnreachable,
      core,
      activeRoom,
      activeRoleLabel,
      telemetry: {
        satellitesOnline: model.rooms.filter((r) => r.online).length,
        satellitesTotal: model.rooms.length,
        peoplePresent,
        occupiedRooms,
        toolsHealthy: model.tools.filter((tool) => tool.health === 'healthy').length,
        toolsTotal: model.tools.length,
      },
    };
  }, [t, model, bootLoading, backendUnreachable, sats]);
}
