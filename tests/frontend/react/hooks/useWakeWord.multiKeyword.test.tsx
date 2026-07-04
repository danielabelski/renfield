import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

/**
 * These tests exercise the LOADED-engine path (isListening === true), which the
 * sibling `useWakeWord.test.tsx` can't reach — there the real WASM engine module
 * isn't installed, so `loadWakeWordEngine()` always fails. Here we mock the
 * engine-loader seam (`wakewordEngineLoader`) with a controllable stub engine so
 * we can assert the multi-keyword contract: the browser loads EVERY server-pushed
 * wake word, and rebuilds the running engine when the active set changes.
 */

// Shared mutable capture state — hoisted so the vi.mock factory can close over it.
const h = vi.hoisted(() => {
  type Listeners = Record<string, ((data?: unknown) => void) | undefined>;
  interface EngineOptions {
    keywords: string[];
    detectionThreshold: number;
    modelFiles?: Record<string, string>;
    baseAssetUrl: string;
    cooldownMs: number;
  }
  const created: Array<{
    opts: EngineOptions;
    load: ReturnType<typeof vi.fn>;
    start: ReturnType<typeof vi.fn>;
    stop: ReturnType<typeof vi.fn>;
    setActiveKeywords: ReturnType<typeof vi.fn>;
    listeners: Listeners;
  }> = [];

  // When set, the NEXT engine's load() blocks on this promise (to test
  // pre-emption of an in-flight arm). Cleared per test.
  const gate: { loadGate: Promise<void> | null } = { loadGate: null };

  class MockEngine {
    listeners: Listeners = {};
    load = vi.fn(() => gate.loadGate ?? Promise.resolve());
    start = vi.fn().mockResolvedValue(undefined);
    stop = vi.fn().mockResolvedValue(undefined);
    setActiveKeywords = vi.fn();
    on = (event: string, cb: (data?: unknown) => void) => {
      this.listeners[event] = cb;
      return () => {
        this.listeners[event] = undefined;
      };
    };
    constructor(opts: EngineOptions) {
      created.push({
        opts,
        load: this.load,
        start: this.start,
        stop: this.stop,
        setActiveKeywords: this.setActiveKeywords,
        listeners: this.listeners,
      });
    }
  }

  return { created, MockEngine, gate };
});

// Mock the engine loader so the engine is "available" and construction is captured.
vi.mock('../../../../src/frontend/src/hooks/wakewordEngineLoader', () => ({
  loadWakeWordEngine: vi.fn(async () => true),
  getWakeWordEngineClass: () => h.MockEngine,
  getWakeWordLoadError: () => null,
  wasWakeWordLoadAttempted: () => true,
}));

// Mock the config with a small, known keyword set.
vi.mock('../../../../src/frontend/src/config/wakeword', () => {
  const availableKeywords = [
    { id: 'hey_jarvis', label: 'Hey Jarvis', model: 'hey_jarvis.onnx', description: 'Test' },
    { id: 'alexa', label: 'Alexa', model: 'alexa.onnx', description: 'Test' },
    { id: 'renfield_de', label: 'Renfield (Deutsch)', model: 'renfield_de.onnx', description: 'Test' },
  ];
  const known = new Set(availableKeywords.map((k) => k.id));
  const parseKeywords = (value?: string | null): string[] => {
    if (!value) return [];
    const seen = new Set<string>();
    const out: string[] = [];
    for (const raw of value.split(',')) {
      const id = raw.trim();
      if (id && known.has(id) && !seen.has(id)) {
        seen.add(id);
        out.push(id);
      }
    }
    return out;
  };
  const sameKeywordSet = (a?: string | null, b?: string | null): boolean => {
    const pa = parseKeywords(a);
    const pb = parseKeywords(b);
    if (pa.length !== pb.length) return false;
    const sb = new Set(pb);
    return pa.every((id) => sb.has(id));
  };
  return {
    WAKEWORD_CONFIG: {
      modelBasePath: '/wakeword-models',
      ortWasmPath: '/ort/',
      availableKeywords,
      defaults: {
        enabled: false,
        keyword: 'hey_jarvis',
        threshold: 0.5,
        cooldownMs: 2000,
        audioFeedback: true,
        gain: 1.0,
      },
      storageKeys: {
        enabled: 'renfield_wakeword_enabled',
        keyword: 'renfield_wakeword_keyword',
        threshold: 'renfield_wakeword_threshold',
        audioFeedback: 'renfield_wakeword_audio_feedback',
      },
    },
    parseKeywords,
    sameKeywordSet,
    describeKeywords: (value?: string | null) =>
      parseKeywords(value)
        .map((id) => availableKeywords.find((k) => k.id === id)?.label ?? id)
        .join(' + '),
    loadWakeWordSettings: vi.fn(() => ({
      enabled: false,
      keyword: 'hey_jarvis',
      threshold: 0.5,
      audioFeedback: true,
    })),
    saveWakeWordSettings: vi.fn(),
  };
});

vi.mock('../../../../src/frontend/src/utils/debug', () => ({
  debug: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

const sortedKeywords = (engine: (typeof h.created)[number]) => [...engine.opts.keywords].sort();

// Dispatch a server config update and drain the fire-and-forget setKeyword() +
// its serialized engine rebuild (a few promise hops) so assertions see the
// settled state.
async function pushConfig(detail: { wake_words?: string[]; threshold?: number }) {
  await act(async () => {
    window.dispatchEvent(new CustomEvent('wakeword-config-update', { detail }));
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  });
}

async function importHook() {
  const mod = await import('../../../../src/frontend/src/hooks/useWakeWord');
  return mod.useWakeWord;
}

describe('useWakeWord — multi-keyword (loaded engine)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.created.length = 0;
    h.gate.loadGate = null;
    localStorage.clear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('enable() builds an engine loaded with the single default keyword', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.enable();
    });

    expect(result.current.isEnabled).toBe(true);
    expect(result.current.isListening).toBe(true);
    expect(h.created).toHaveLength(1);
    expect(h.created[0].opts.keywords).toEqual(['hey_jarvis']);
    expect(h.created[0].start).toHaveBeenCalled();
  });

  it('a server config push with multiple wake_words rebuilds the engine with ALL of them', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.enable();
    });
    const firstEngine = h.created[0];

    await pushConfig({ wake_words: ['hey_jarvis', 'alexa', 'renfield_de'] });

    // A NEW engine was built (setActiveKeywords can't load new models).
    expect(h.created.length).toBe(2);
    expect(sortedKeywords(h.created[1])).toEqual(['alexa', 'hey_jarvis', 'renfield_de']);
    // Old engine torn down, new one started and listening.
    expect(firstEngine.stop).toHaveBeenCalled();
    expect(h.created[1].start).toHaveBeenCalled();
    expect(result.current.isListening).toBe(true);
  });

  it('drops server-pushed keywords with no shipped model, keeps the rest', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.enable();
    });

    await pushConfig({ wake_words: ['renfield_de', 'nonexistent_keyword'] });

    expect(h.created.length).toBe(2);
    expect(sortedKeywords(h.created[1])).toEqual(['renfield_de']);
  });

  it('a config push with the SAME set (reordered) does not rebuild', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.setKeyword('hey_jarvis,alexa');
      await result.current.enable();
    });
    expect(h.created.length).toBe(1);
    expect(sortedKeywords(h.created[0])).toEqual(['alexa', 'hey_jarvis']);

    await pushConfig({ wake_words: ['alexa', 'hey_jarvis'] }); // same set, different order

    // No rebuild — still the one engine.
    expect(h.created.length).toBe(1);
  });

  it('setKeyword persists the full set and rebuilds while listening', async () => {
    const useWakeWord = await importHook();
    const { saveWakeWordSettings } = await import('../../../../src/frontend/src/config/wakeword');
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.enable();
    });

    await act(async () => {
      await result.current.setKeyword('hey_jarvis,renfield_de');
    });

    expect(saveWakeWordSettings).toHaveBeenCalledWith({ keyword: 'hey_jarvis,renfield_de' });
    expect(result.current.settings.keyword).toBe('hey_jarvis,renfield_de');
    expect(h.created.length).toBe(2);
    expect(sortedKeywords(h.created[1])).toEqual(['hey_jarvis', 'renfield_de']);
    expect(result.current.isListening).toBe(true);
  });

  it('a config change while paused rebuilds on the NEXT resume() with the new set', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.enable();
    });
    expect(h.created.length).toBe(1);

    // Pause (keeps engine), then a multi-keyword config arrives while paused.
    await act(async () => {
      await result.current.pause();
    });
    expect(result.current.isListening).toBe(false);

    await pushConfig({ wake_words: ['hey_jarvis', 'alexa'] });
    // While paused we drop the stale engine but don't start a new one yet.
    expect(result.current.isListening).toBe(false);

    await act(async () => {
      await result.current.resume();
    });

    // resume rebuilt with the new set.
    expect(result.current.isListening).toBe(true);
    const latest = h.created[h.created.length - 1];
    expect(sortedKeywords(latest)).toEqual(['alexa', 'hey_jarvis']);
    expect(latest.start).toHaveBeenCalled();
  });

  it('detection fires the callback for any keyword in the loaded set', async () => {
    const onWakeWordDetected = vi.fn();
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord({ onWakeWordDetected }));

    await act(async () => {
      await result.current.setKeyword('hey_jarvis,alexa');
      await result.current.enable();
    });

    // Simulate the engine detecting the SECOND keyword.
    await act(async () => {
      h.created[0].listeners.detect?.({ keyword: 'alexa', score: 0.9, at: 1234 });
    });

    expect(onWakeWordDetected).toHaveBeenCalledWith('alexa', 0.9);
    expect(result.current.lastDetection?.keyword).toBe('alexa');
  });

  // ---- regression tests for the /review findings ----

  it('disable() turns the wake word off from the PAUSED state (not just while listening)', async () => {
    const useWakeWord = await importHook();
    const { saveWakeWordSettings } = await import('../../../../src/frontend/src/config/wakeword');
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.enable();
      await result.current.pause();
    });
    expect(result.current.isEnabled).toBe(true);
    expect(result.current.isListening).toBe(false);

    await act(async () => {
      await result.current.disable();
    });

    expect(result.current.isEnabled).toBe(false);
    expect(h.created[0].stop).toHaveBeenCalled();
    expect(saveWakeWordSettings).toHaveBeenCalledWith({ enabled: false });
  });

  it('an engine error drops the dead engine and keeps intent, so resume() rebuilds a fresh one', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.enable();
    });
    const firstEngine = h.created[0];

    // The engine emits a fatal error (WASM crash / closed AudioContext).
    await act(async () => {
      firstEngine.listeners.error?.(new Error('engine crashed'));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(result.current.isListening).toBe(false);
    expect(result.current.isEnabled).toBe(true); // intent preserved
    expect(result.current.error).toBeTruthy();
    expect(firstEngine.stop).toHaveBeenCalled(); // dead engine torn down

    // A recovery trigger resumes — it must build a NEW engine, not start the dead one.
    await act(async () => {
      await result.current.resume();
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(h.created.length).toBe(2);
    expect(h.created[1].start).toHaveBeenCalled();
    expect(result.current.isListening).toBe(true);
  });

  it('a config push racing an in-flight enable() ends on the pushed set (serialized, not lost)', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      // Start enable() but do NOT await — the config push lands mid-start, the
      // exact race a multi-language household hits on page load.
      const enabling = result.current.enable();
      window.dispatchEvent(
        new CustomEvent('wakeword-config-update', {
          detail: { wake_words: ['hey_jarvis', 'alexa'] },
        }),
      );
      await enabling;
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    });

    // Serialization guarantees the keyword change applied AFTER enable finished,
    // so the live engine loads the pushed multi set — never the stale default.
    const latest = h.created[h.created.length - 1];
    expect(sortedKeywords(latest)).toEqual(['alexa', 'hey_jarvis']);
    expect(latest.start).toHaveBeenCalled();
    expect(result.current.isListening).toBe(true);
  });

  it('disable() pre-empts an in-flight enable() (a hung load never blocks mic-off)', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    // The next engine's load() hangs until we release it.
    let releaseLoad!: () => void;
    h.gate.loadGate = new Promise<void>((r) => {
      releaseLoad = r;
    });

    let enabling: Promise<void>;
    await act(async () => {
      enabling = result.current.enable();
      await Promise.resolve(); // let the arm reach the hung load()
    });

    // Turn it off while enable is stuck loading — disable must NOT be queued
    // behind the hung arm.
    await act(async () => {
      await result.current.disable();
    });
    expect(result.current.isEnabled).toBe(false);
    expect(result.current.isListening).toBe(false);

    // Release the hung load: the superseded arm must discard its engine and
    // never open the mic (never call start()).
    await act(async () => {
      h.gate.loadGate = null;
      releaseLoad();
      await enabling;
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(h.created.every((e) => e.start.mock.calls.length === 0)).toBe(true);
    expect(result.current.isListening).toBe(false);
    expect(result.current.isEnabled).toBe(false);
  });

  it('unmounting during an in-flight enable() stops the discarded engine (no mic leak)', async () => {
    const useWakeWord = await importHook();
    const { result, unmount } = renderHook(() => useWakeWord());

    let releaseLoad!: () => void;
    h.gate.loadGate = new Promise<void>((r) => {
      releaseLoad = r;
    });

    let enabling: Promise<void>;
    await act(async () => {
      enabling = result.current.enable();
      await Promise.resolve();
    });

    // Navigate away mid-build.
    unmount();

    await act(async () => {
      h.gate.loadGate = null;
      releaseLoad();
      await enabling;
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
    });

    // The engine built after unmount must have been stopped, never started.
    const built = h.created[0];
    expect(built.start).not.toHaveBeenCalled();
    expect(built.stop).toHaveBeenCalled();
  });

  it('an error during an in-flight arm never leaves a green-but-dead UI', async () => {
    const useWakeWord = await importHook();
    const { result } = renderHook(() => useWakeWord());

    await act(async () => {
      await result.current.enable();
    });
    const engine = h.created[0];

    // Error fires AFTER start resolved (the worklet reports a runtime glitch).
    await act(async () => {
      engine.listeners.error?.(new Error('runtime glitch'));
      await new Promise((r) => setTimeout(r, 0));
    });

    // The UI must be honest: not listening, and the engine dropped so recovery
    // resume() can rebuild (never isListening=true with a null engine).
    expect(result.current.isListening).toBe(false);
    expect(engine.stop).toHaveBeenCalled();

    await act(async () => {
      await result.current.resume();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(h.created.length).toBe(2);
    expect(result.current.isListening).toBe(true);
  });
});
