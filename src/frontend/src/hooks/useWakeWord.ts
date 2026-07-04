import { useState, useEffect, useRef, useCallback } from 'react';
import {
  WAKEWORD_CONFIG,
  loadWakeWordSettings,
  saveWakeWordSettings,
  parseKeywords,
  sameKeywordSet,
  type WakeWordSettings,
  type KeywordConfig,
} from '../config/wakeword';
import {
  loadWakeWordEngine,
  getWakeWordEngineClass,
  getWakeWordLoadError,
  wasWakeWordLoadAttempted,
  type WakeWordEngine,
} from './wakewordEngineLoader';
import { debug } from '../utils/debug';

// Detection result
interface WakeWordDetection {
  keyword: string;
  score: number;
  timestamp: number;
}

// Hook options
interface UseWakeWordOptions {
  onWakeWordDetected?: (keyword: string, score: number) => void;
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
  onError?: (error: Error) => void;
  onReady?: () => void;
}

// Hook return type
export interface UseWakeWordResult {
  isEnabled: boolean;
  isListening: boolean;
  isLoading: boolean;
  isReady: boolean;
  isAvailable: boolean;
  lastDetection: WakeWordDetection | null;
  error: Error | null;
  settings: WakeWordSettings;
  enable: () => Promise<void>;
  disable: () => Promise<void>;
  toggle: () => Promise<void>;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  setKeyword: (keyword: string) => Promise<void>;
  setThreshold: (threshold: number) => void;
  availableKeywords: KeywordConfig[];
}

/**
 * React hook for wake word detection using OpenWakeWord WASM.
 *
 * Supports a **multi-keyword** active set: `settings.keyword` may be a single id
 * or a comma-separated set (the same form the server settings page uses). The
 * engine loads one model per id, so a German+English household detects every
 * pushed wake word — satellites already do this; the browser now matches them.
 *
 * The engine's `start()` opens a fresh mic + AudioContext and `stop()` closes
 * them, so every pause/resume already rebuilds the audio graph — adding/removing
 * a keyword just means recreating the engine (initEngine loads the full set).
 *
 * **All engine lifecycle transitions (enable/disable/pause/resume/keyword-change/
 * error-recovery) run through `runExclusive`, a promise chain that serializes
 * them.** Without it, a server config push (WS `config_update`) that lands mid
 * enable()/resume() could tear the half-built engine out from under the in-flight
 * start — the exact race a multi-language household hits on page load.
 */
export function useWakeWord({
  onWakeWordDetected,
  onSpeechStart,
  onSpeechEnd,
  onError,
  onReady,
}: UseWakeWordOptions = {}): UseWakeWordResult {
  // State
  const [isEnabled, setIsEnabled] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const [lastDetection, setLastDetection] = useState<WakeWordDetection | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [settings, setSettings] = useState<WakeWordSettings>(() => loadWakeWordSettings());
  const [isAvailable, setIsAvailable] = useState(
    !wasWakeWordLoadAttempted() || getWakeWordEngineClass() !== null,
  );

  // Refs
  const engineRef = useRef<WakeWordEngine | null>(null);
  const unsubscribersRef = useRef<Array<() => void>>([]);
  const callbacksRef = useRef({ onWakeWordDetected, onSpeechStart, onSpeechEnd, onError, onReady });
  // Live mirrors of the enabled/listening state, updated SYNCHRONOUSLY by the
  // transitions below so a serialized op reads the true current state (not the
  // render closure it was created in) when it finally runs.
  const isEnabledRef = useRef(false);
  const isListeningRef = useRef(false);
  // The active keyword id set + threshold, mirrored into refs so the engine
  // build path (initEngine) reads the CURRENT values synchronously.
  const keywordsRef = useRef<string[]>(parseKeywords(settings.keyword));
  const thresholdRef = useRef<number>(settings.threshold);

  // Serializes every engine lifecycle transition. Each queued op runs only
  // after the previous one settles (success OR failure), so no two transitions
  // ever touch engineRef concurrently. Errors don't poison the chain.
  const opChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const runExclusive = useCallback((op: () => Promise<void>): Promise<void> => {
    const run = opChainRef.current.then(op, op);
    opChainRef.current = run.catch(() => undefined);
    return run;
  }, []);

  // Keep callbacks ref updated
  useEffect(() => {
    callbacksRef.current = { onWakeWordDetected, onSpeechStart, onSpeechEnd, onError, onReady };
  }, [onWakeWordDetected, onSpeechStart, onSpeechEnd, onError, onReady]);

  // Keep the engine-build refs in sync with settings (belt-and-suspenders: the
  // mutators update these synchronously; this catches any external settings set)
  useEffect(() => {
    keywordsRef.current = parseKeywords(settings.keyword);
    thresholdRef.current = settings.threshold;
  }, [settings.keyword, settings.threshold]);

  // Build a fresh engine from the CURRENT keyword set + threshold (read from
  // refs, so this never captures a stale set).
  const initEngine = useCallback(async (): Promise<WakeWordEngine> => {
    const EngineClass = getWakeWordEngineClass();
    if (!EngineClass) {
      throw new Error('Wake word detection not available. Please rebuild the application.');
    }

    // Build model file map from config (includes custom keywords like hey_renfield)
    const modelFiles: Record<string, string> = {};
    for (const kw of WAKEWORD_CONFIG.availableKeywords) {
      modelFiles[kw.id] = kw.model;
    }

    // Load EVERY active keyword's model (multi-language household). Fall back to
    // the default single keyword if the set somehow resolved to nothing.
    const keywords = keywordsRef.current.length
      ? [...keywordsRef.current]
      : [WAKEWORD_CONFIG.defaults.keyword];

    return new EngineClass({
      baseAssetUrl: WAKEWORD_CONFIG.modelBasePath,
      keywords,
      modelFiles,
      detectionThreshold: thresholdRef.current,
      cooldownMs: WAKEWORD_CONFIG.defaults.cooldownMs,
    });
  }, []);

  // Fully tear down the engine: unsubscribe, stop (closes mic + AudioContext),
  // drop the ref. Low-level primitive — only called from inside runExclusive
  // (or unmount cleanup).
  const teardownEngine = useCallback(async () => {
    unsubscribersRef.current.forEach((unsub) => unsub?.());
    unsubscribersRef.current = [];
    const engine = engineRef.current;
    engineRef.current = null;
    setIsReady(false);
    if (engine) {
      try {
        await engine.stop();
      } catch (err) {
        console.error('Failed to stop wake word engine:', err);
      }
    }
  }, []);

  // Build + load + subscribe an engine if none exists. Low-level primitive.
  const buildEngine = useCallback(async () => {
    if (engineRef.current) return;
    const engine = await initEngine();
    await engine.load();

    // Wire the engine's events to hook state/callbacks.
    const unsubReady = engine.on('ready', () => {
      setIsReady(true);
      callbacksRef.current.onReady?.();
    });
    const unsubDetect = engine.on('detect', ({ keyword, score, at }) => {
      const detection: WakeWordDetection = { keyword, score, timestamp: at || Date.now() };
      setLastDetection(detection);
      callbacksRef.current.onWakeWordDetected?.(keyword, score);
    });
    const unsubSpeechStart = engine.on('speech-start', () => {
      callbacksRef.current.onSpeechStart?.();
    });
    const unsubSpeechEnd = engine.on('speech-end', () => {
      callbacksRef.current.onSpeechEnd?.();
    });
    const unsubError = engine.on('error', (err: Error) => {
      setError(err);
      // The engine errored — it is no longer detecting. Reflect that in
      // isListening (it previously stayed stale-true, lying to the UI) so the
      // status dot goes yellow AND the recovery triggers in ChatContext
      // (WS-reconnect / tab-visible / network-online) can resume it. isEnabled
      // stays true so the user's intent is preserved and the resume path stays
      // open. DROP the dead engine (serialized) so recovery resume() rebuilds a
      // fresh one rather than start()-ing the broken instance.
      isListeningRef.current = false;
      setIsListening(false);
      callbacksRef.current.onError?.(err);
      void runExclusive(() => teardownEngine());
    });

    unsubscribersRef.current = [
      unsubReady,
      unsubDetect,
      unsubSpeechStart,
      unsubSpeechEnd,
      unsubError,
    ];
    engineRef.current = engine;
  }, [initEngine, runExclusive, teardownEngine]);

  // Map raw engine/browser errors to friendly, actionable messages.
  const reportEnableError = useCallback((err: unknown) => {
    console.error('Failed to enable wake word:', err);
    const raw = err instanceof Error ? err : new Error(String(err));

    let out = raw;
    if (raw.message && raw.message.includes('sample-rate')) {
      // Firefox: AudioContext sample rate mismatch
      out = new Error(
        'Wake word detection is not supported in Firefox due to AudioContext sample rate limitations. ' +
          'Please use Chrome or Edge for wake word detection, or use the manual recording button.',
      );
      out.name = 'BrowserNotSupportedError';
    } else if (
      raw.message &&
      (raw.message.includes('SharedArrayBuffer') ||
        raw.message.includes('cross-origin isolated') ||
        raw.message.includes('CompileError') ||
        raw.message.includes('WebAssembly'))
    ) {
      // Safari/WebKit: WASM or SharedArrayBuffer issues
      out = new Error(
        'Wake word detection requires WebAssembly threading which may not be available in this browser. ' +
          'Please use Chrome or Edge, or use the manual recording button.',
      );
      out.name = 'BrowserNotSupportedError';
    }

    setError(out);
    callbacksRef.current.onError?.(out);
  }, []);

  // Enable wake word listening.
  const enable = useCallback(() => {
    // Flip the loading indicator synchronously (before the op is queued) so the
    // spinner appears on the click, not a microtask later.
    setIsLoading(true);
    setError(null);
    return runExclusive(async () => {
      try {
        if (isEnabledRef.current && isListeningRef.current) return;

        // Lazy load the wake word engine module
        const loaded = await loadWakeWordEngine();
        if (!loaded) {
          setIsAvailable(false);
          const err =
            getWakeWordLoadError() ||
            new Error(
              'Wake word detection not available. Please run: npm install && docker compose up -d --build',
            );
          setError(err);
          callbacksRef.current.onError?.(err);
          return;
        }
        setIsAvailable(true);

        await buildEngine();
        await engineRef.current!.start({ gain: WAKEWORD_CONFIG.defaults.gain });

        isEnabledRef.current = true;
        isListeningRef.current = true;
        setIsEnabled(true);
        setIsListening(true);
        saveWakeWordSettings({ enabled: true });
      } catch (err) {
        // Drop any half-built engine so a retry rebuilds cleanly.
        await teardownEngine();
        isListeningRef.current = false;
        setIsListening(false);
        reportEnableError(err);
      } finally {
        setIsLoading(false);
      }
    });
  }, [runExclusive, buildEngine, teardownEngine, reportEnableError]);

  // Disable wake word listening. Works from ANY state (listening, paused, or
  // post-error) — the guard is "is there anything to turn off?", not isListening,
  // so the toggle can always turn the mic off (and clear the persisted flag).
  const disable = useCallback(
    () =>
      runExclusive(async () => {
        if (!isEnabledRef.current && !engineRef.current) return;
        await teardownEngine();
        isEnabledRef.current = false;
        isListeningRef.current = false;
        setIsEnabled(false);
        setIsListening(false);
        saveWakeWordSettings({ enabled: false });
      }),
    [runExclusive, teardownEngine],
  );

  // Toggle wake word
  const toggle = useCallback(async () => {
    if (isEnabled) {
      await disable();
    } else {
      await enable();
    }
  }, [isEnabled, enable, disable]);

  // Pause listening temporarily (e.g., while recording). Serialized, so it runs
  // AFTER any in-flight rebuild — it never sees a transient null engine.
  const pause = useCallback(
    () =>
      runExclusive(async () => {
        if (!isListeningRef.current || !engineRef.current) {
          debug.log('⚠️ pause() skipped: not listening or no engine');
          return;
        }
        try {
          await engineRef.current.stop();
          isListeningRef.current = false;
          setIsListening(false);
          debug.log('✅ Wake word paused (isEnabled stays true)');
        } catch (err) {
          console.error('Failed to pause wake word:', err);
        }
      }),
    [runExclusive],
  );

  // Resume listening after pause. buildEngine() rebuilds if the engine was
  // dropped (keyword change while paused, or an error), then start()s — start
  // always opens a fresh mic + AudioContext, so a rebuild here is no different
  // from a normal resume.
  const resume = useCallback(
    () =>
      runExclusive(async () => {
        if (isListeningRef.current) return;
        if (!isEnabledRef.current) return;

        try {
          await buildEngine();
          await engineRef.current!.start({ gain: WAKEWORD_CONFIG.defaults.gain });
          isListeningRef.current = true;
          setIsListening(true);
          debug.log('✅ Wake word engine resumed');
        } catch (err) {
          console.error('Failed to resume wake word:', err);
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      }),
    [runExclusive, buildEngine],
  );

  // Rebuild the engine to match the current keyword set. Serialized. Because the
  // engine can only load models chosen at construction, changing the set means a
  // full stop → drop → recreate. Preserves the listening/paused state.
  const applyKeywordChange = useCallback(
    () =>
      runExclusive(async () => {
        // Not running: just drop any stale engine — enable() will build fresh
        // with the new set.
        if (!isEnabledRef.current) {
          await teardownEngine();
          return;
        }
        const wasListening = isListeningRef.current;
        await teardownEngine();
        if (wasListening) {
          try {
            await buildEngine();
            await engineRef.current!.start({ gain: WAKEWORD_CONFIG.defaults.gain });
            isListeningRef.current = true;
            setIsListening(true);
          } catch (err) {
            console.error('Failed to rebuild wake word engine:', err);
            setError(err instanceof Error ? err : new Error(String(err)));
            isListeningRef.current = false;
            setIsListening(false);
          }
        }
        // If paused: leave the engine dropped; resume() rebuilds with the new set.
      }),
    [runExclusive, teardownEngine, buildEngine],
  );

  // Update the active keyword set. Accepts a single id or a comma-separated set.
  // Persists it (survives reload) and, if the set actually changed, rebuilds the
  // engine so every keyword is loaded.
  const setKeyword = useCallback(
    async (keyword: string) => {
      const ids = parseKeywords(keyword);
      // Ignore a selection that resolves to nothing we ship a model for — keep
      // the current set rather than blanking detection.
      if (ids.length === 0) return;
      const normalized = ids.join(',');
      if (sameKeywordSet(normalized, keywordsRef.current.join(','))) return;

      keywordsRef.current = ids; // synchronous — the rebuild below reads this
      setSettings((prev) => ({ ...prev, keyword: normalized }));
      saveWakeWordSettings({ keyword: normalized });

      await applyKeywordChange();
    },
    [applyKeywordChange],
  );

  // Update threshold. Takes effect on the next engine build (kept in a ref so a
  // rebuild picks it up); we don't force a rebuild for a sensitivity nudge.
  const setThreshold = useCallback((threshold: number) => {
    thresholdRef.current = threshold;
    setSettings((prev) => ({ ...prev, threshold }));
    saveWakeWordSettings({ threshold });
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      unsubscribersRef.current.forEach((unsub) => unsub?.());
      if (engineRef.current) {
        engineRef.current.stop().catch(() => {});
        engineRef.current = null;
      }
    };
  }, []);

  // Auto-enable if was previously enabled
  useEffect(() => {
    const savedSettings = loadWakeWordSettings();
    if (savedSettings.enabled && !isEnabled && !isLoading) {
      // Delay auto-enable to allow page to fully load
      const timer = setTimeout(() => {
        enable();
      }, 1000);
      return () => clearTimeout(timer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Only run once on mount

  // Listen for config updates from server (via WebSocket). Loads the FULL
  // server-pushed wake_words set, not just the first (multi-language household).
  useEffect(() => {
    const handleConfigUpdate = (event: CustomEvent<{ wake_words?: string[]; threshold?: number }>) => {
      const config = event.detail;
      debug.log('🔄 Wake word config update from server:', config);

      if (config.wake_words && config.wake_words.length > 0) {
        const ids = parseKeywords(config.wake_words.join(','));
        if (ids.length > 0 && !sameKeywordSet(ids.join(','), keywordsRef.current.join(','))) {
          debug.log(
            `🎤 Updating wake words: [${keywordsRef.current.join(', ')}] -> [${ids.join(', ')}]`,
          );
          void setKeyword(ids.join(','));
        }
      }

      if (config.threshold !== undefined && config.threshold !== thresholdRef.current) {
        debug.log(`🎚️ Updating threshold: ${thresholdRef.current} -> ${config.threshold}`);
        setThreshold(config.threshold);
      }
    };

    window.addEventListener('wakeword-config-update', handleConfigUpdate as EventListener);
    return () => window.removeEventListener('wakeword-config-update', handleConfigUpdate as EventListener);
  }, [setKeyword, setThreshold]);

  return {
    // State
    isEnabled,
    isListening,
    isLoading,
    isReady,
    isAvailable,
    lastDetection,
    error,
    settings,

    // Controls
    enable,
    disable,
    toggle,
    pause,
    resume,
    setKeyword,
    setThreshold,

    // Config access
    availableKeywords: WAKEWORD_CONFIG.availableKeywords,
  };
}

export default useWakeWord;
