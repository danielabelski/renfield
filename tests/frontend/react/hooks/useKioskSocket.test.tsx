/**
 * useKioskSocket — the PUSH data source for /kiosk. Covers the reducer folding
 * (snapshot hydration, each delta, unknown-event tolerance) and the reconnect
 * lifecycle (a dropped socket reconnects with backoff and re-hydrates from the
 * fresh snapshot the hub sends on every connect).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useKioskSocket } from '../../../../src/frontend/src/components/command-center/useKioskSocket';

type WsListener<E = unknown> = ((event: E) => void) | null;

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;

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
  fireClose(): void {
    this.readyState = 3;
    const ev =
      typeof CloseEvent !== 'undefined'
        ? new CloseEvent('close')
        : (new Event('close') as unknown as CloseEvent);
    this.onclose?.(ev);
  }
  // The hook's cleanup calls close(); real close() completes async, so it does
  // NOT synchronously fire onclose here.
  close(): void {
    this.readyState = 3;
  }
}

function latest(): MockWebSocket {
  return MockWebSocket.instances[MockWebSocket.instances.length - 1];
}

const NOW_SEC = Date.now() / 1000;

function baseSnapshot() {
  return {
    type: 'snapshot',
    at: '2026-07-04T21:00:00Z',
    satellites: [
      { satellite_id: 'sat-wz', room: 'Wohnzimmer', room_id: 1, state: 'idle', last_heartbeat: NOW_SEC },
      { satellite_id: 'sat-ez', room: 'Esszimmer', room_id: 2, state: 'idle', last_heartbeat: NOW_SEC - 4000 },
    ],
    presence: {
      rooms: [{ room_id: 1, room_name: 'Wohnzimmer', occupants: 2 }],
      people_present: 2,
      occupied_rooms: 1,
    },
    mcp: {
      enabled: true,
      total_tools: 12,
      servers: [{ name: 'homeassistant', connected: true, transport: 'stdio', tool_count: 10 }],
    },
    tool_health: [{ tool_name: 'mcp.homeassistant.turn_on', total: 10, success_rate: 1, degraded: false }],
    roles: [
      { name: 'general', description: { de: 'Allgemein', en: 'General' }, mcp_servers: null, internal_tools: null, has_agent_loop: true },
    ],
    activity: [{ role: 'general', at: '2026-07-04T20:59:00Z', ok: true }],
    peers: [{ id: 'p1', name: 'Peer', last_seen_at: '2026-07-04T20:58:00Z', reachable: true }],
    weather: { location: 'Musterstadt', temp: 20, unit: '°C', code: 0, condition: 'Klar', high: 22, low: 12 },
    now_playing: [{ room: 'Wohnzimmer', kind: 'radio', title: 'Radio', subtitle: null, track: null, total: null }],
  };
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('useKioskSocket', () => {
  it('opens /ws/kiosk and starts unhydrated (connecting)', () => {
    const { result } = renderHook(() => useKioskSocket());
    expect(latest().url).toContain('/ws/kiosk');
    expect(result.current.status).toBe('connecting');
    expect(result.current.bootLoading).toBe(true);
    expect(result.current.live.hydrated).toBe(false);
  });

  it('hydrates the full model from the snapshot message', () => {
    const { result } = renderHook(() => useKioskSocket());
    act(() => {
      latest().fireOpen();
      latest().fireMessage(baseSnapshot());
    });

    expect(result.current.status).toBe('open');
    expect(result.current.bootLoading).toBe(false);
    const m = result.current.live;
    expect(m.hydrated).toBe(true);
    expect(m.satellites).toHaveLength(2);
    // heartbeat_ago is derived from last_heartbeat: the fresh one is ~0, the
    // 4000s-old one is large (→ offline downstream).
    expect(m.satellites[0].heartbeat_ago_seconds).toBeLessThan(90);
    expect(m.satellites[1].heartbeat_ago_seconds).toBeGreaterThan(90);
    expect(m.presence.people_present).toBe(2);
    expect(m.mcp.servers[0].name).toBe('homeassistant');
    expect(m.toolHealth).toHaveLength(1);
    expect(m.roles[0].name).toBe('general');
    expect(m.activity[0].role).toBe('general');
    expect(m.peers[0].name).toBe('Peer');
    expect(m.weather?.location).toBe('Musterstadt');
    expect(m.nowPlaying[0].title).toBe('Radio');
  });

  it('folds a satellite_state delta onto the matching satellite', () => {
    const { result } = renderHook(() => useKioskSocket());
    act(() => {
      latest().fireOpen();
      latest().fireMessage(baseSnapshot());
    });
    act(() => {
      latest().fireMessage({ type: 'satellite_state', satellite_id: 'sat-wz', room: 'Wohnzimmer', room_id: 1, state: 'listening' });
    });
    const wz = result.current.live.satellites.find((s) => s.satellite_id === 'sat-wz');
    expect(wz?.state).toBe('listening');
    // untouched satellite keeps its state
    expect(result.current.live.satellites.find((s) => s.satellite_id === 'sat-ez')?.state).toBe('idle');
  });

  it('appends a satellite_state delta for a satellite not in the snapshot', () => {
    const { result } = renderHook(() => useKioskSocket());
    act(() => {
      latest().fireOpen();
      latest().fireMessage(baseSnapshot());
    });
    act(() => {
      latest().fireMessage({ type: 'satellite_state', satellite_id: 'sat-new', room: 'Küche', room_id: 9, state: 'speaking' });
    });
    const added = result.current.live.satellites.find((s) => s.satellite_id === 'sat-new');
    expect(added?.state).toBe('speaking');
    expect(added?.heartbeat_ago_seconds).toBe(0); // treated as freshly live
  });

  it('folds a turn_activity delta into the trail and the subsystem pulses', () => {
    const { result } = renderHook(() => useKioskSocket());
    act(() => {
      latest().fireOpen();
      latest().fireMessage(baseSnapshot());
    });
    act(() => {
      latest().fireMessage({ type: 'turn_activity', role: 'smart_home', subsystems: ['homeassistant', 'weather'], ok: true, at: '2026-07-04T21:01:00Z' });
    });
    // newest activity prepended
    expect(result.current.live.activity[0].role).toBe('smart_home');
    // each named subsystem stamped with the event time
    const pulses = result.current.live.subsystemPulses;
    expect(pulses.homeassistant).toBe(Date.parse('2026-07-04T21:01:00Z'));
    expect(pulses.weather).toBe(Date.parse('2026-07-04T21:01:00Z'));
  });

  it('ignores an unknown event type without crashing or mutating state', () => {
    const { result } = renderHook(() => useKioskSocket());
    act(() => {
      latest().fireOpen();
      latest().fireMessage(baseSnapshot());
    });
    const before = result.current.live;
    act(() => {
      latest().fireMessage({ type: 'peer_status_changed', peer_id: 'p1', reachable: false });
      latest().fireMessage({ type: 'something_from_phase_9' });
    });
    // reference unchanged → no re-render churn, and definitely no throw
    expect(result.current.live).toBe(before);
  });

  it('reconnects with backoff and re-hydrates from a fresh snapshot', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useKioskSocket());
    act(() => {
      latest().fireOpen();
      latest().fireMessage(baseSnapshot());
    });
    expect(MockWebSocket.instances).toHaveLength(1);

    // socket drops → reconnecting, no new socket yet
    act(() => {
      latest().fireClose();
    });
    expect(result.current.status).toBe('reconnecting');
    expect(MockWebSocket.instances).toHaveLength(1);

    // first backoff (1s) elapses → a fresh socket is opened
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(MockWebSocket.instances).toHaveLength(2);

    // the hub re-sends a snapshot on connect → the missed-event gap self-heals
    act(() => {
      latest().fireOpen();
      latest().fireMessage({ ...baseSnapshot(), satellites: [{ satellite_id: 'sat-wz', room: 'Wohnzimmer', room_id: 1, state: 'speaking', last_heartbeat: Date.now() / 1000 }] });
    });
    expect(result.current.status).toBe('open');
    expect(result.current.live.satellites).toHaveLength(1);
    expect(result.current.live.satellites[0].state).toBe('speaking');
  });
});
