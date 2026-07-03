/**
 * KioskPage — the fullscreen wall-display command center. Smoke coverage:
 * it composes the same sources as the admin board and derives the voice-core
 * state from the satellites' own state (idle vs listening).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../test-utils';
import { server } from '../mocks/server';
import KioskPage from '../../../../src/frontend/src/pages/KioskPage';
import { TEST_CONFIG } from '../config';

const BASE = TEST_CONFIG.API_BASE_URL;

const roles = [
  { name: 'presence', description: { de: 'Presence', en: 'Presence' }, mcp_servers: [], internal_tools: null, has_agent_loop: true },
  { name: 'general', description: { de: 'General', en: 'General' }, mcp_servers: null, internal_tools: null, has_agent_loop: true },
];
const mcp = {
  enabled: true, total_tools: 20,
  servers: [
    { name: 'homeassistant', connected: true, transport: 'stdio', tool_count: 10 },
    { name: 'radio', connected: false, transport: 'stdio', tool_count: 0 },
  ],
};

function mockAll(satState: string) {
  server.use(
    http.get(`${BASE}/api/command-center/roles`, () => HttpResponse.json(roles)),
    http.get(`${BASE}/api/command-center/activity`, () => HttpResponse.json([])),
    http.get(`${BASE}/api/mcp/status`, () => HttpResponse.json(mcp)),
    http.get(`${BASE}/api/tool-health`, () => HttpResponse.json([])),
    http.get(`${BASE}/api/satellites`, () => HttpResponse.json({
      satellites: [
        { satellite_id: 'sat-wohnzimmer', room: 'Wohnzimmer', state: satState, uptime_seconds: 100, heartbeat_ago_seconds: 4 },
        { satellite_id: 'sat-esszimmer', room: 'Esszimmer', state: 'idle', uptime_seconds: 100, heartbeat_ago_seconds: 8 },
      ],
      total_count: 2, online_count: 2, active_sessions: 0, latest_version: '1.4.1',
    })),
    http.get(`${BASE}/api/presence/rooms`, () => HttpResponse.json([
      { room_id: 1, room_name: 'Wohnzimmer', occupants: [{ user_id: 1, last_seen: 0, confidence: 0.9 }] },
    ])),
    http.get(`${BASE}/api/federation/peers`, () => HttpResponse.json({ peers: [] })),
  );
}

afterEach(() => server.resetHandlers());

describe('KioskPage', () => {
  // Tests run in the German locale (test-utils sets it), so captions are the
  // German strings: idle → "bereit", listening → "hört zu", healthy → "gesund".
  it('renders the fullscreen kiosk with wordmark, telemetry and rings', async () => {
    mockAll('idle');
    renderWithProviders(<KioskPage />);
    await waitFor(() => {
      expect(screen.getAllByText('RENFIELD').length).toBeGreaterThan(0);
    });
    // ambient telemetry corner (content-free counts): both satellites online
    expect(screen.getByText('2/2 online')).toBeInTheDocument();
    // tools: 1 healthy of 2 (radio down)
    expect(screen.getByText('1/2 gesund')).toBeInTheDocument();
    const svg = document.querySelector('svg[role="img"]') as SVGElement;
    expect(svg).toBeTruthy();
  });

  it('drives the core into a voice state from a listening satellite', async () => {
    mockAll('listening');
    renderWithProviders(<KioskPage />);
    await waitFor(() => {
      expect(screen.getAllByText(/hört zu/i).length).toBeGreaterThan(0);
    });
    // active room is surfaced content-free (room name only)
    expect(screen.getAllByText(/Wohnzimmer/).length).toBeGreaterThan(0);
  });

  it('shows the ready core when every satellite is idle', async () => {
    mockAll('idle');
    renderWithProviders(<KioskPage />);
    await waitFor(() => {
      expect(screen.getAllByText(/bereit/i).length).toBeGreaterThan(0);
    });
  });
});
