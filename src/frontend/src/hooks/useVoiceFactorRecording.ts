/**
 * Aufnahme für den zweiten Anmeldefaktor — Mikrofon rein, Blob raus.
 *
 * WARUM NICHT `useAudioRecording`
 * ------------------------------
 * Der Chat-Hook (`pages/ChatPage/hooks/useAudioRecording.ts`) ist für Diktat
 * gebaut und endet in einem POST an `/api/voice/stt`. Zwei seiner Eigenschaften
 * sind hier direkt falsch:
 *
 * 1. **Er schneidet per VAD bei Stille ab.** Eine Anmeldephrase mit einer kurzen
 *    Pause darin wäre abgeschnitten — und je kürzer die Aufnahme, desto
 *    schlechter trennt der Stimmvergleich.
 * 2. **Er transkribiert.** Der zweite Faktor braucht die ROHE Aufnahme; ein
 *    Text hilft ihm nicht, und der Umweg über die Spracherkennung wäre ein
 *    zusätzlicher Dienst im Anmeldeweg.
 *
 * Deshalb ein eigener, kleiner Hook: kein VAD, keine Transkription, dafür eine
 * MINDEST- und eine Höchstdauer. Die ~40 Zeilen `MediaRecorder`-Gerüst ähneln
 * dem Chat-Hook; das ist Gerüst, nicht Geschäftslogik. Wer den Chat-Hook das
 * nächste Mal anfasst, kann den gemeinsamen Kern herausziehen — vorher wäre es
 * ein Umbau an einem Pfad, den `ChatContext` benutzt, für einen Gewinn von
 * vierzig Zeilen.
 *
 * 🛑 DIE MINDESTDAUER IST KEINE SICHERHEITSPRÜFUNG
 * -----------------------------------------------
 * Sie ist Bedienführung: eine halbe Sekunde Audio ergibt einen schlechten
 * Vergleich, und der Nutzer soll das vor dem Absenden merken, statt eine
 * Ablehnung zu kassieren. Ein Angreifer schickt ohnehin direkt an die API. Die
 * serverseitige Dauerschranke (H3 des Reviews vom 2026-09-27) ist ein eigener,
 * offener Posten und wird hiervon NICHT ersetzt.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

/** Mindestdauer, ab der das Absenden freigeschaltet wird. */
export const MIN_DURATION_MS = 1500;
/** Höchstdauer — danach stoppt die Aufnahme selbst. */
export const MAX_DURATION_MS = 8000;

export type RecordingError =
  /** Nutzer hat das Mikrofon verweigert oder der Browser blockiert es. */
  | 'permission'
  /** Kein Mikrofon vorhanden. */
  | 'no-device'
  /** Browser kann nicht aufnehmen (kein MediaRecorder / kein getUserMedia). */
  | 'unsupported'
  /** Alles andere. */
  | 'unknown';

interface State {
  recording: boolean;
  /** 0..1, für die Aussteuerungsanzeige. */
  level: number;
  /** Bisherige Dauer in Millisekunden. */
  elapsedMs: number;
  /** Fertige Aufnahme, sobald gestoppt. */
  blob: Blob | null;
  error: RecordingError | null;
}

const INITIAL: State = {
  recording: false, level: 0, elapsedMs: 0, blob: null, error: null,
};

interface AudioContextCapableWindow {
  AudioContext?: typeof AudioContext;
  webkitAudioContext?: typeof AudioContext;
}

export function useVoiceFactorRecording() {
  const [state, setState] = useState<State>(INITIAL);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const frameRef = useRef<number | null>(null);
  const startedAtRef = useRef<number>(0);
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Verhindert, dass ein `setState` nach dem Unmount läuft (React 18 StrictMode
  // montiert zweimal, und das Mikrofon darf dabei nicht offen bleiben).
  const aliveRef = useRef(true);

  const teardown = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    // 🛑 Die Spuren MÜSSEN einzeln gestoppt werden, sonst bleibt die
    // Mikrofonanzeige des Browsers an — auf einer ANMELDESEITE ist ein
    // heimlich offenes Mikrofon das Letzte, was man will.
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      void audioCtxRef.current.close();
    }
    audioCtxRef.current = null;
    analyserRef.current = null;
    recorderRef.current = null;
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      teardown();
    };
  }, [teardown]);

  const stop = useCallback(() => {
    const rec = recorderRef.current;
    if (rec && rec.state !== 'inactive') {
      rec.stop();   // `onstop` setzt den Blob und räumt auf
    }
  }, []);

  const start = useCallback(async () => {
    if (state.recording) return;

    const hasMedia = typeof navigator !== 'undefined'
      && !!navigator.mediaDevices?.getUserMedia
      && typeof MediaRecorder !== 'undefined';
    if (!hasMedia) {
      setState({ ...INITIAL, error: 'unsupported' });
      return;
    }

    setState({ ...INITIAL, recording: true });
    chunksRef.current = [];

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // Dieselben Einstellungen wie der Chat-Pfad: das Modell hat seine
        // Referenzprofile aus so aufgenommenem Material.
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      const name = (err as { name?: string })?.name ?? '';
      // Diese Fälle sind ABSICHTLICH unterscheidbar: es geht um das Gerät des
      // Nutzers, nicht um ein Geheimnis des Servers. Wer nicht weiß, dass er das
      // Mikrofon verweigert hat, kann es nicht erlauben.
      const error: RecordingError =
        name === 'NotAllowedError' || name === 'SecurityError' ? 'permission'
          : name === 'NotFoundError' || name === 'DevicesNotFoundError' ? 'no-device'
            : 'unknown';
      if (aliveRef.current) setState({ ...INITIAL, error });
      return;
    }

    if (!aliveRef.current) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    streamRef.current = stream;

    // Aussteuerung, damit der Nutzer sieht, dass etwas ankommt. Rein visuell —
    // es hängt keine Entscheidung daran.
    try {
      const w = window as unknown as AudioContextCapableWindow;
      const Ctor = w.AudioContext ?? w.webkitAudioContext;
      if (Ctor) {
        const ctx = new Ctor();
        audioCtxRef.current = ctx;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(analyser);
        analyserRef.current = analyser;
      }
    } catch {
      // Ohne Anzeige ist die Aufnahme trotzdem gültig — kein Abbruch.
      analyserRef.current = null;
    }

    startedAtRef.current = Date.now();
    const data = analyserRef.current
      ? new Uint8Array(analyserRef.current.frequencyBinCount)
      : null;

    const tick = () => {
      if (!aliveRef.current) return;
      let level = 0;
      if (analyserRef.current && data) {
        analyserRef.current.getByteFrequencyData(data);
        let sum = 0;
        for (const v of data) sum += v;
        level = Math.min(1, sum / data.length / 128);
      }
      setState((s) => (s.recording
        ? { ...s, level, elapsedMs: Date.now() - startedAtRef.current }
        : s));
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);

    const recorder = new MediaRecorder(stream);
    recorderRef.current = recorder;
    recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
      const elapsedMs = Date.now() - startedAtRef.current;
      teardown();
      if (aliveRef.current) {
        setState({ recording: false, level: 0, elapsedMs, blob, error: null });
      }
    };
    recorder.start();

    // Selbststopp an der Höchstdauer. Ohne das läuft das Mikrofon, bis jemand
    // den Knopf wiederfindet.
    stopTimerRef.current = setTimeout(() => {
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    }, MAX_DURATION_MS);
  }, [state.recording, teardown]);

  const reset = useCallback(() => {
    teardown();
    setState(INITIAL);
  }, [teardown]);

  return {
    ...state,
    /** Lang genug, um sinnvoll verglichen zu werden. */
    longEnough: state.elapsedMs >= MIN_DURATION_MS,
    start,
    stop,
    reset,
  };
}
