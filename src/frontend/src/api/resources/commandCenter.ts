import apiClient from '../../utils/axios';
import { useApiQuery } from '../hooks';
import { keys, STALE } from '../keys';
import type { McpStatus } from './integrations';

/**
 * Command Center feeds (docs/design/command-center.md).
 *
 * `/api/command-center/roles` — the agent roles the router currently serves
 * (from agent_roles.yaml, availability-filtered). `mcp_servers`/`internal_tools`
 * back the hover reach-edges; `null` means "may use everything".
 *
 * `/api/command-center/activity` — the content-free live pulse: recent role
 * activations (role + timestamp + ok), newest first. Polled at LIVE cadence so
 * the board breathes with real household activity.
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

async function fetchAgentRoles(): Promise<AgentRoleInfo[]> {
  const response = await apiClient.get<AgentRoleInfo[]>('/api/command-center/roles');
  return response.data ?? [];
}

async function fetchRoleActivity(limit: number): Promise<RoleActivityEntry[]> {
  const response = await apiClient.get<RoleActivityEntry[]>(
    '/api/command-center/activity',
    { params: { limit } },
  );
  return response.data ?? [];
}

export function useAgentRolesQuery() {
  return useApiQuery(
    {
      queryKey: keys.commandCenter.roles(),
      queryFn: fetchAgentRoles,
      staleTime: STALE.CONFIG,
    },
    'commandCenter.rolesLoadError',
  );
}

/** MCP status WITHOUT the error-swallowing of useMcpStatusQuery — the board's
 *  per-ring error treatment needs a query that can actually report isError
 *  (integrations.ts resolves failures to an empty status, which would render
 *  as "no integrations configured" instead of "the status feed is failing"). */
export function useMcpStatusStrictQuery() {
  return useApiQuery(
    {
      queryKey: [...keys.commandCenter.all, 'mcpStatus'] as const,
      queryFn: async () => {
        const response = await apiClient.get<McpStatus>('/api/mcp/status');
        return response.data;
      },
      staleTime: STALE.DEFAULT,
    },
    'integrations.loadError',
  );
}

/** The pulse poll. 3s keeps the board feeling live without meaningful load
 *  (one bounded read of recent assistant-message metadata). */
export function useRoleActivityQuery(limit = 30) {
  return useApiQuery(
    {
      queryKey: [...keys.commandCenter.activity(), { limit }] as const,
      queryFn: () => fetchRoleActivity(limit),
      staleTime: STALE.LIVE,
      refetchInterval: 3000,
    },
    'commandCenter.activityLoadError',
  );
}
