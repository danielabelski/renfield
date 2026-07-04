/**
 * Wake Word Detection Configuration
 *
 * Configuration for the OpenWakeWord WASM browser-based wake word detection.
 * Models are loaded from /public/wakeword-models/
 */

// Keyword configuration type
export interface KeywordConfig {
  id: string;
  label: string;
  model: string;
  description: string;
}

// Wake word settings type
export interface WakeWordSettings {
  enabled: boolean;
  keyword: string;
  threshold: number;
  audioFeedback: boolean;
}

// Storage keys type
interface StorageKeys {
  enabled: string;
  keyword: string;
  threshold: string;
  audioFeedback: string;
}

// Wake word defaults type
interface WakeWordDefaults {
  enabled: boolean;
  keyword: string;
  threshold: number;
  cooldownMs: number;
  audioFeedback: boolean;
  gain: number;
}

// Main config type
export interface WakeWordConfigType {
  modelBasePath: string;
  ortWasmPath: string;
  availableKeywords: KeywordConfig[];
  defaults: WakeWordDefaults;
  storageKeys: StorageKeys;
  vadHangoverFrames: number;
  activationDelayMs: number;
}

export const WAKEWORD_CONFIG: WakeWordConfigType = {
  // Path to ONNX model files (relative to public folder)
  modelBasePath: '/wakeword-models',

  // Path to ONNX Runtime WASM files (relative to public folder)
  ortWasmPath: '/ort/',

  // Available wake words with their model files
  // Add custom trained models here (e.g., hey_renfield.onnx)
  availableKeywords: [
    {
      id: 'hey_renfield',
      label: 'Hey Renfield',
      model: 'hey_renfield.onnx',
      description: 'Custom trained wake word'
    },
    {
      id: 'renfield_de',
      label: 'Renfield (Deutsch)',
      model: 'renfield_de.onnx',
      description: 'German single-word wake word'
    },
    {
      id: 'renfield_en',
      label: 'Renfield (English)',
      model: 'renfield_en.onnx',
      description: 'English (US+UK) single-word wake word'
    },
    {
      id: 'renfield_it',
      label: 'Renfield (Italiano)',
      model: 'renfield_it.onnx',
      description: 'Italian single-word wake word'
    },
    {
      id: 'hey_jarvis',
      label: 'Hey Jarvis',
      model: 'hey_jarvis_v0.1.onnx',
      description: 'Pre-trained wake word'
    },
    {
      id: 'alexa',
      label: 'Alexa',
      model: 'alexa_v0.1.onnx',
      description: 'Pre-trained wake word'
    },
    {
      id: 'hey_mycroft',
      label: 'Hey Mycroft',
      model: 'hey_mycroft_v0.1.onnx',
      description: 'Pre-trained wake word'
    },
  ],

  // Default settings
  defaults: {
    enabled: false,           // Disabled by default (opt-in for privacy)
    keyword: 'hey_renfield',  // Default wake word
    threshold: 0.5,           // Detection sensitivity (0.0 - 1.0)
    cooldownMs: 2000,         // Minimum ms between detections
    audioFeedback: true,      // Play sound on detection
    gain: 1.0,                // Microphone gain
  },

  // LocalStorage keys for persisting settings
  storageKeys: {
    enabled: 'renfield_wakeword_enabled',
    keyword: 'renfield_wakeword_keyword',
    threshold: 'renfield_wakeword_threshold',
    audioFeedback: 'renfield_wakeword_audio_feedback',
  },

  // Performance tuning
  // VAD hangover frames - keeps speech detection open long enough for wake word
  vadHangoverFrames: 12,

  // Delay after wake word before starting recording (let wake word audio finish)
  activationDelayMs: 300,
};

/**
 * Get the model file path for a keyword
 */
export function getModelPath(keywordId: string): string | null {
  const keyword = WAKEWORD_CONFIG.availableKeywords.find(k => k.id === keywordId);
  if (!keyword) return null;
  return `${WAKEWORD_CONFIG.modelBasePath}/${keyword.model}`;
}

/**
 * Get keyword configuration by ID
 */
export function getKeywordConfig(keywordId: string): KeywordConfig | null {
  return WAKEWORD_CONFIG.availableKeywords.find(k => k.id === keywordId) || null;
}

/**
 * Parse a keyword selection into a clean, ordered, de-duplicated list of
 * keyword ids we actually ship a model for. The browser engine loads one model
 * per id, so an unknown id (e.g. a server pushing a wake word this build has no
 * `.onnx` for) is dropped rather than crashing the load.
 *
 * The value may be a single id (`"renfield_de"`) or a comma-separated set
 * (`"renfield_de,renfield_en"`) — the same on-the-wire form the server settings
 * page uses — so a multi-language household loads every keyword together.
 */
export function parseKeywords(value: string | null | undefined): string[] {
  if (!value) return [];
  const known = new Set(WAKEWORD_CONFIG.availableKeywords.map((k) => k.id));
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
}

/**
 * Whether two keyword selections resolve to the same set (order/whitespace/
 * duplicate/unknown-id insensitive). Used to avoid rebuilding the running
 * engine when a server config push doesn't actually change the active set.
 */
export function sameKeywordSet(a: string | null | undefined, b: string | null | undefined): boolean {
  const pa = parseKeywords(a);
  const pb = parseKeywords(b);
  if (pa.length !== pb.length) return false;
  const sb = new Set(pb);
  return pa.every((id) => sb.has(id));
}

/**
 * Human-readable label for a keyword selection — joins each active keyword's
 * label so the UI can honestly show a multi-keyword set (e.g. "Renfield
 * (Deutsch) + Renfield (English)") instead of only the first.
 */
export function describeKeywords(value: string | null | undefined): string {
  return parseKeywords(value)
    .map((id) => getKeywordConfig(id)?.label ?? id)
    .join(' + ');
}

/**
 * Load saved wake word settings from localStorage
 */
export function loadWakeWordSettings(): WakeWordSettings {
  const { defaults, storageKeys } = WAKEWORD_CONFIG;

  try {
    return {
      enabled: localStorage.getItem(storageKeys.enabled) === 'true',
      keyword: localStorage.getItem(storageKeys.keyword) || defaults.keyword,
      threshold: parseFloat(localStorage.getItem(storageKeys.threshold) || '') || defaults.threshold,
      audioFeedback: localStorage.getItem(storageKeys.audioFeedback) !== 'false',
    };
  } catch {
    // localStorage not available (e.g., private browsing)
    return {
      enabled: defaults.enabled,
      keyword: defaults.keyword,
      threshold: defaults.threshold,
      audioFeedback: defaults.audioFeedback,
    };
  }
}

/**
 * Save wake word settings to localStorage
 */
export function saveWakeWordSettings(settings: Partial<WakeWordSettings>): void {
  const { storageKeys } = WAKEWORD_CONFIG;

  try {
    if (settings.enabled !== undefined) {
      localStorage.setItem(storageKeys.enabled, String(settings.enabled));
    }
    if (settings.keyword !== undefined) {
      localStorage.setItem(storageKeys.keyword, settings.keyword);
    }
    if (settings.threshold !== undefined) {
      localStorage.setItem(storageKeys.threshold, String(settings.threshold));
    }
    if (settings.audioFeedback !== undefined) {
      localStorage.setItem(storageKeys.audioFeedback, String(settings.audioFeedback));
    }
  } catch {
    // localStorage not available
    console.warn('Could not save wake word settings to localStorage');
  }
}

export default WAKEWORD_CONFIG;
