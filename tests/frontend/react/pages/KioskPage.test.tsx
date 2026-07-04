/**
 * KioskPage — the fullscreen wall-display command center. Now fed by the PUSH
 * socket (useKioskSocket) instead of react-query polls: the fixtures moved from
 * MSW HTTP handlers to a `snapshot` message pushed over a mock WebSocket. The
 * derivation math (voice-core priority, telemetry counts) is unchanged, so this
 * is the same behavioural coverage — only the data source differs.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, act } from '@testing-library/react';
import { renderWithProviders } from '../test-utils';
import KioskPage from '../../../../src/frontend/src/pages/KioskPage';

type WsListener<E = unknown> = ((event: E) => void) | null;

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  url: string;
  readyState = 0;
  onopen: WsListener<Event> = null;
  onclose: WsListener<CloseEvent> = null;
  onmessage: WsListener<MessageEvent> = null;
  onerror: WsListener<Event> = null;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  fireOpen(): void {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }
  fireMessage(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
  send(): void {}
  close(): void {
    this.readyState = 3;
  }
}

const NOW_SEC = Date.now() / 1000;

const roles = [
  { name: 'presence', description: { de: 'Presence', en: 'Presence' }, mcp_servers: [], internal_tools: null, has_agent_loop: true },
  { name: 'general', description: { de: 'General', en: 'General' }, mcp_servers: null, internal_tools: null, has_agent_loop: true },
];
const mcp = {
  enabled: true,
  total_tools: 20,
  servers: [
    { name: 'homeassistant', connected: true, transport: 'stdio', tool_count: 10 },
    { name: 'radio', connected: false, transport: 'stdio', tool_count: 0 },
  ],
};

interface SnapOverrides {
  satellites?: unknown[];
  weather?: unknown;
  now_playing?: unknown[];
}

function snapshot(satState: string, over: SnapOverrides = {}) {
  return {
    type: 'snapshot',
    at: '2026-07-04T21:00:00Z',
    satellites: over.satellites ?? [
      { satellite_id: 'sat-wohnzimmer', room: 'Wohnzimmer', room_id: 1, state: satState, last_heartbeat: NOW_SEC },
      { satellite_id: 'sat-esszimmer', room: 'Esszimmer', room_id: 2, state: 'idle', last_heartbeat: NOW_SEC },
    ],
    presence: {
      rooms: [{ room_id: 1, room_name: 'Wohnzimmer', occupants: 1 }],
      people_present: 1,
      occupied_rooms: 1,
    },
    mcp,
    tool_health: [],
    roles,
    activity: [],
    peers: [],
    weather: over.weather ?? null,
    now_playing: over.now_playing ?? [],
  };
}

function pushSnapshot(snap: unknown) {
  const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
  act(() => {
    ws.fireOpen();
    ws.fireMessage(snap);
  });
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('KioskPage', () => {
  // The core no longer renders a state WORD (its LED colour conveys the state);
  // the live state is exposed as `data-core-state` on the core group.
  const coreState = () =>
    document.querySelector('[data-core-state]')?.getAttribute('data-core-state');

  it('renders the fullscreen kiosk with wordmark, telemetry and rings', async () => {
    renderWithProviders(<KioskPage />);
    pushSnapshot(snapshot('idle'));
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
    renderWithProviders(<KioskPage />);
    pushSnapshot(snapshot('listening'));
    await waitFor(() => {
      expect(coreState()).toBe('listening');
    });
    expect(screen.getAllByText(/Wohnzimmer/).length).toBeGreaterThan(0);
  });

  it('shows the ready core when every satellite is idle', async () => {
    renderWithProviders(<KioskPage />);
    pushSnapshot(snapshot('idle'));
    await waitFor(() => {
      expect(coreState()).toBe('idle');
    });
  });

  it('counts the real satellite list and ignores a stale satellite in the core', async () => {
    renderWithProviders(<KioskPage />);
    // 3 satellites: two share a room, one is stale (heartbeat > 90s) AND still
    // reporting 'listening' — it must not count as online nor drive the core.
    pushSnapshot(snapshot('idle', {
      satellites: [
        { satellite_id: 'a', room: 'Wohnzimmer', room_id: 1, state: 'idle', last_heartbeat: NOW_SEC },
        { satellite_id: 'b', room: 'Wohnzimmer', room_id: 1, state: 'idle', last_heartbeat: NOW_SEC },
        { satellite_id: 'c', room: 'Esszimmer', room_id: 2, state: 'listening', last_heartbeat: NOW_SEC - 4000 },
      ],
    }));
    await waitFor(() => expect(screen.getByText('2/3 online')).toBeInTheDocument());
    expect(coreState()).toBe('idle');
  });

  it('shows the weather tile when a reading is available', async () => {
    renderWithProviders(<KioskPage />);
    pushSnapshot(snapshot('idle', {
      weather: { location: 'Musterstadt', temp: 21.4, unit: '°C', code: 0, condition: 'Klarer Himmel', high: 24, low: 13 },
    }));
    await waitFor(() => expect(screen.getByText('21°C')).toBeInTheDocument());
    expect(screen.getByText(/Klarer Himmel/)).toBeInTheDocument();
    expect(screen.getByText(/Musterstadt/)).toBeInTheDocument();
  });

  it('shows the now-playing tile for a live media session', async () => {
    renderWithProviders(<KioskPage />);
    pushSnapshot(snapshot('idle', {
      now_playing: [{ room: 'Wohnzimmer', kind: 'radio', title: 'Radio Beispiel', subtitle: null, track: null, total: null }],
    }));
    await waitFor(() => expect(screen.getByText('Radio Beispiel')).toBeInTheDocument());
    expect(screen.getAllByText(/Wohnzimmer/).length).toBeGreaterThan(0);
  });

  it('surfaces a live-satellite error as busy, not a false ready', async () => {
    renderWithProviders(<KioskPage />);
    pushSnapshot(snapshot('idle', {
      satellites: [{ satellite_id: 'a', room: 'Wohnzimmer', room_id: 1, state: 'error', last_heartbeat: NOW_SEC }],
    }));
    await waitFor(() => {
      expect(coreState()).toBe('busy');
    });
  });

  it('pulses the MCP node named by a live turn_activity delta', async () => {
    renderWithProviders(<KioskPage />);
    pushSnapshot(snapshot('idle'));
    await waitFor(() => expect(screen.getByText('2/2 online')).toBeInTheDocument());
    // no pulse yet
    expect(document.querySelector('[data-tool-id="homeassistant"][data-tool-active="1"]')).toBeNull();
    // a turn touches Home Assistant → its node lights up
    const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
    act(() => {
      ws.fireMessage({ type: 'turn_activity', role: 'smart_home', subsystems: ['homeassistant'], ok: true, at: new Date().toISOString() });
    });
    await waitFor(() =>
      expect(document.querySelector('[data-tool-id="homeassistant"][data-tool-active="1"]')).not.toBeNull(),
    );
  });
});
