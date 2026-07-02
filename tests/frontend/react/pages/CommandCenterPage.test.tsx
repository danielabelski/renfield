/**
 * CommandCenterPage — the /admin/command-center live constellation
 * (docs/design/command-center.md Phase 1+2).
 *
 * Covers: model assembly from the six sources, the constellation board
 * (roles/tools/rooms/peers + active-role pulse from the activity feed),
 * drill-down links, health mapping (down/degraded), the activity rail,
 * and the backend-unreachable calm state.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../test-utils';
import { server } from '../mocks/server';
import CommandCenterPage from '../../../../src/frontend/src/pages/CommandCenterPage';
import { TEST_CONFIG } from '../config';

const BASE = TEST_CONFIG.API_BASE_URL;

const agentRoles = [
  {
    name: 'smart_home',
    description: { de: 'Smart Home', en: 'Smart home' },
    mcp_servers: ['homeassistant'],
    internal_tools: ['internal.device_action'],
    has_agent_loop: true,
  },
  {
    name: 'knowledge',
    description: { de: 'Wissen', en: 'Knowledge' },
    mcp_servers: [],
    internal_tools: ['internal.knowledge_search'],
    has_agent_loop: false,
  },
  {
    name: 'general',
    description: { de: 'Allgemein', en: 'General' },
    mcp_servers: null,
    internal_tools: null,
    has_agent_loop: true,
  },
];

function isoSecondsAgo(seconds: number): string {
  // The API emits naive-UTC timestamps (no zone suffix) — mirror that.
  return new Date(Date.now() - seconds * 1000).toISOString().replace('Z', '');
}

const mcpStatus = {
  enabled: true,
  total_tools: 30,
  servers: [
    { name: 'homeassistant', connected: true, transport: 'stdio', tool_count: 20 },
    { name: 'paperless', connected: true, transport: 'stdio', tool_count: 8, last_error: 'timeout' },
    { name: 'radio', connected: false, transport: 'stdio', tool_count: 0 },
  ],
};

const satellites = {
  satellites: [
    {
      satellite_id: 'sat-wohnzimmer',
      room: 'Wohnzimmer',
      state: 'idle',
      uptime_seconds: 100,
      heartbeat_ago_seconds: 5,
    },
    {
      satellite_id: 'sat-fitnessraum',
      room: 'Fitnessraum',
      state: 'error',
      uptime_seconds: 0,
      heartbeat_ago_seconds: 4000,
    },
  ],
  total_count: 2,
  online_count: 1,
  active_sessions: 0,
  latest_version: '1.4.1',
};

const presenceRooms = [
  {
    room_id: 1,
    room_name: 'Wohnzimmer',
    occupants: [
      { user_id: 1, user_name: 'A', last_seen: 0, confidence: 0.9 },
      { user_id: 2, user_name: 'B', last_seen: 0, confidence: 0.8 },
    ],
  },
];

function mockAll({ activity = [] as Array<{ role: string; at: string; ok: boolean | null }> } = {}) {
  server.use(
    http.get(`${BASE}/api/command-center/roles`, () => HttpResponse.json(agentRoles)),
    http.get(`${BASE}/api/command-center/activity`, () => HttpResponse.json(activity)),
    http.get(`${BASE}/api/mcp/status`, () => HttpResponse.json(mcpStatus)),
    http.get(`${BASE}/api/tool-health`, () => HttpResponse.json([])),
    http.get(`${BASE}/api/satellites`, () => HttpResponse.json(satellites)),
    http.get(`${BASE}/api/presence/rooms`, () => HttpResponse.json(presenceRooms)),
    http.get(`${BASE}/api/federation/peers`, () =>
      HttpResponse.json({
        peers: [
          {
            id: 1,
            remote_display_name: 'Reva',
            remote_pubkey: 'pk',
            circle_tier: 3,
            last_seen_at: new Date().toISOString(),
          },
        ],
      }),
    ),
  );
}

afterEach(() => {
  server.resetHandlers();
});

describe('CommandCenterPage', () => {
  beforeEach(() => {
    mockAll();
  });

  it('renders the constellation with all four rings from live data', async () => {
    renderWithProviders(<CommandCenterPage />);
    // roles ring (localized labels from chat.roles.*)
    await waitFor(() => {
      expect(screen.getAllByText('Wohnzimmer').length).toBeGreaterThan(0);
    });
    // tools ring: server names prettified
    expect(screen.getAllByText('Homeassistant').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Paperless').length).toBeGreaterThan(0);
    // peers arc
    expect(screen.getAllByText('Reva').length).toBeGreaterThan(0);
    // core wordmark
    expect(screen.getAllByText('Renfield').length).toBeGreaterThan(0);
  });

  it('shows occupant count for occupied rooms', async () => {
    renderWithProviders(<CommandCenterPage />);
    await waitFor(() => {
      expect(screen.getAllByText('Wohnzimmer').length).toBeGreaterThan(0);
    });
    const svg = document.querySelector('svg[role="img"]') as SVGElement;
    expect(svg).toBeTruthy();
    // Wohnzimmer has 2 occupants — the count renders inside the room node
    expect(within(svg as unknown as HTMLElement).getByText('2')).toBeInTheDocument();
  });

  it('marks the active role from a fresh activity entry (core + rail)', async () => {
    mockAll({
      activity: [
        { role: 'smart_home', at: isoSecondsAgo(10), ok: true },
        { role: 'knowledge', at: isoSecondsAgo(120), ok: null },
      ],
    });
    renderWithProviders(<CommandCenterPage />);
    await waitFor(() => {
      expect(screen.getAllByText('Renfield').length).toBeGreaterThan(0);
    });
    const svg = document.querySelector('svg[role="img"]') as SVGElement;
    // the core subtext names the active role (localized smart_home label)
    const activeLabel = within(svg as unknown as HTMLElement).getAllByText(
      /smart home/i,
    );
    expect(activeLabel.length).toBeGreaterThan(0);
    // the activity rail lists both turns, content-free
    const rail = screen.getByLabelText(/live activity|live-aktivität/i);
    expect(within(rail).getAllByText(/smart home/i).length).toBeGreaterThan(0);
  });

  it('renders idle core when the last activation is stale', async () => {
    mockAll({ activity: [{ role: 'smart_home', at: isoSecondsAgo(600), ok: true }] });
    renderWithProviders(<CommandCenterPage />);
    await waitFor(() => {
      expect(screen.getAllByText(/idle|inaktiv/i).length).toBeGreaterThan(0);
    });
  });

  it('drill-down nodes are keyboard-focusable links with aria labels', async () => {
    renderWithProviders(<CommandCenterPage />);
    await waitFor(() => {
      expect(screen.getAllByText('Homeassistant').length).toBeGreaterThan(0);
    });
    const svg = document.querySelector('svg[role="img"]') as SVGElement;
    const links = within(svg as unknown as HTMLElement).getAllByRole('link');
    // 3 roles + 3 tools + 2 rooms + 1 peer
    expect(links.length).toBe(9);
    for (const link of links) {
      expect(link.getAttribute('tabindex')).toBe('0');
      expect(link.getAttribute('aria-label')).toBeTruthy();
    }
  });

  it('shows the at-a-glance summary with healthy/total counts', async () => {
    renderWithProviders(<CommandCenterPage />);
    await waitFor(() => {
      // homeassistant healthy; paperless degraded (last_error); radio down
      expect(screen.getByText('1/3')).toBeInTheDocument();
    });
    // one of two rooms online (fitnessraum heartbeat is stale)
    expect(screen.getByText('1/2')).toBeInTheDocument();
  });

  it('shows the empty activity state when nothing happened', async () => {
    renderWithProviders(<CommandCenterPage />);
    await waitFor(() => {
      expect(
        screen.getByText(/quiet right now|gerade ruhig/i),
      ).toBeInTheDocument();
    });
  });

  it('shows the calm unreachable banner when every source fails', async () => {
    server.use(
      http.get(`${BASE}/api/command-center/roles`, () => HttpResponse.error()),
      http.get(`${BASE}/api/command-center/activity`, () => HttpResponse.error()),
      http.get(`${BASE}/api/mcp/status`, () => HttpResponse.error()),
      http.get(`${BASE}/api/tool-health`, () => HttpResponse.error()),
      http.get(`${BASE}/api/satellites`, () => HttpResponse.error()),
      http.get(`${BASE}/api/presence/rooms`, () => HttpResponse.error()),
      http.get(`${BASE}/api/federation/peers`, () => HttpResponse.error()),
    );
    renderWithProviders(<CommandCenterPage />);
    await waitFor(
      () => {
        expect(
          screen.getByText(/backend unreachable|backend nicht erreichbar/i),
        ).toBeInTheDocument();
      },
      { timeout: 8000 },
    );
  });
});
