/**
 * Lazy loader + singleton for the OpenWakeWord WASM engine class.
 *
 * Extracted from `useWakeWord` so the dynamic `import('openwakeword-wasm-browser')`
 * lives behind a small, resolvable module seam. That keeps the hook free of a
 * module-level singleton AND makes the "engine loaded successfully" path
 * testable: a test can `vi.mock('.../wakewordEngineLoader')` to inject a stub
 * engine class without the real WASM module (which isn't installed in the test
 * environment) — the previous harness could only ever exercise the load-failure
 * path.
 */
import { debug } from '../utils/debug';

// Wake word engine interface (from openwakeword-wasm-browser)
export interface WakeWordEngine {
  load(): Promise<void>;
  start(options?: { gain?: number }): Promise<void>;
  stop(): Promise<void>;
  setActiveKeywords(keywords: string[]): void;
  on(event: 'ready', callback: () => void): () => void;
  on(event: 'detect', callback: (data: { keyword: string; score: number; at?: number }) => void): () => void;
  on(event: 'speech-start', callback: () => void): () => void;
  on(event: 'speech-end', callback: () => void): () => void;
  on(event: 'error', callback: (error: Error) => void): () => void;
}

export interface WakeWordEngineConstructor {
  new (options: {
    baseAssetUrl: string;
    keywords: string[];
    /**
     * Optional override of the built-in keyword -> model-file map. Required
     * for custom wake words like `hey_renfield` which aren't in the
     * library's default `MODEL_FILE_MAP`.
     */
    modelFiles?: Record<string, string>;
    detectionThreshold: number;
    cooldownMs: number;
  }): WakeWordEngine;
}

let WakeWordEngineClass: WakeWordEngineConstructor | null = null;
let loadAttempted = false;
let loadError: Error | null = null;

/**
 * Lazy load the wake word engine module. Idempotent: after the first attempt it
 * returns the cached result. Resolves `true` when the engine class is available.
 */
export async function loadWakeWordEngine(): Promise<boolean> {
  if (loadAttempted) {
    return WakeWordEngineClass !== null;
  }

  loadAttempted = true;

  try {
    // Configure ONNX Runtime before importing the engine
    const ort = await import('onnxruntime-web');

    // Disable multi-threading to avoid SharedArrayBuffer requirement
    ort.env.wasm.numThreads = 1;

    // Disable proxy mode - it causes dynamic import issues with Vite
    ort.env.wasm.proxy = false;

    // Set explicit WASM file paths to avoid Vite module interception
    ort.env.wasm.wasmPaths = '/ort/';

    debug.log('✅ ONNX Runtime (WASM) configured with paths:', ort.env.wasm.wasmPaths);

    const module = await import('openwakeword-wasm-browser');
    WakeWordEngineClass = module.default || module.WakeWordEngine;
    debug.log('✅ Wake word engine loaded successfully');
    return WakeWordEngineClass !== null;
  } catch (e) {
    loadError = e instanceof Error ? e : new Error(String(e));
    console.warn('⚠️ openwakeword-wasm-browser not available:', loadError.message);
    console.warn('💡 Run: npm install && docker compose up -d --build');
    return false;
  }
}

/** The loaded engine class, or null before a successful load. */
export function getWakeWordEngineClass(): WakeWordEngineConstructor | null {
  return WakeWordEngineClass;
}

/** The error from a failed load attempt, if any. */
export function getWakeWordLoadError(): Error | null {
  return loadError;
}

/** Whether a load has been attempted (drives the initial `isAvailable`). */
export function wasWakeWordLoadAttempted(): boolean {
  return loadAttempted;
}
