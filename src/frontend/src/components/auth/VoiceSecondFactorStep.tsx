/**
 * Der zweite Schritt der Anmeldung: Stimme aufnehmen und bestätigen.
 *
 * Erscheint, wenn `/auth/login` `SecondFactorRequired` wirft — das Passwort war
 * richtig, die Anmeldung ist noch nicht fertig. Diese Maske nimmt auf und schickt
 * Ticket + Aufnahme an `POST /api/auth/voice`.
 *
 * 🛑 WAS HIER ABSICHTLICH NICHT STEHT
 * ----------------------------------
 * Der Server antwortet auf JEDEN Fehlschlag identisch
 * (`{"success": false, "message": "Voice authentication failed"}`) — kein Feld
 * unterscheidet „Ticket abgelaufen" von „Stimme passt nicht" von „Konto
 * gesperrt". Das ist der Orakel-Schutz, und diese Maske darf ihn nicht
 * untergraben: sie zeigt die Server-Meldung NICHT durch und rät auch nicht,
 * welcher Fall es war.
 *
 * Unterscheidbar sind nur Dinge, die dem NUTZER gehören: ein verweigertes
 * Mikrofon, ein fehlendes Gerät, ein Browser, der nicht aufnehmen kann. Das sind
 * keine Geheimnisse des Servers, und wer nicht weiß, dass er das Mikrofon
 * verweigert hat, kann es nicht erlauben.
 *
 * 🛑 UND WAS ES NICHT GIBT: einen Weg vorbei. Es gibt keinen „ohne Stimme
 * anmelden"-Knopf, auch nicht nach mehreren Fehlversuchen — ein Rückfall, den
 * ein Angreifer selbst auslöst, hebt den Faktor auf. Nach dem dritten Versuch
 * nennt die Maske den vorgesehenen Weg: ein Administrator schaltet den Faktor
 * für dieses Konto ab.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Check, Mic, Square } from 'lucide-react';

import apiClient from '../../utils/axios';
import {
  MAX_DURATION_MS,
  MIN_DURATION_MS,
  useVoiceFactorRecording,
} from '../../hooks/useVoiceFactorRecording';

interface Props {
  /** Das Einmalticket aus `/auth/login`. */
  ticket: string;
  /** Beide Faktoren bestanden — der Aufrufer holt den Nutzer und navigiert. */
  onVerified: () => void | Promise<void>;
  /** Abbrechen: zurück zur Passworteingabe. Das Ticket verfällt von selbst. */
  onCancel: () => void;
}

/** Ab so vielen Fehlversuchen wird der Wiederherstellungsweg genannt. */
const HINT_AFTER_ATTEMPTS = 3;

export default function VoiceSecondFactorStep({ ticket, onVerified, onCancel }: Props) {
  const { t } = useTranslation();
  const rec = useVoiceFactorRecording();
  const [submitting, setSubmitting] = useState(false);
  const [refused, setRefused] = useState(false);
  const [attempts, setAttempts] = useState(0);
  const recordButtonRef = useRef<HTMLButtonElement | null>(null);

  // Fokus auf den Aufnahmeknopf, sobald der Schritt erscheint: wer mit der
  // Tastatur anmeldet, soll nicht suchen müssen.
  useEffect(() => {
    recordButtonRef.current?.focus();
  }, []);

  const submit = async () => {
    if (!rec.blob) return;
    setSubmitting(true);
    setRefused(false);
    try {
      const form = new FormData();
      form.append('ticket', ticket);
      form.append('audio_file', rec.blob, 'second-factor.webm');
      // 🛑 Der Kopf MUSS gesetzt werden. `apiClient` traegt
      // `Content-Type: application/json` als Voreinstellung (`utils/axios.ts`),
      // und axios entfernt den bei FormData nur, wenn er NICHT explizit gesetzt
      // ist. Ohne diese Zeile ist der Koerper multipart, der Kopf behauptet
      // JSON, und FastAPI antwortet 422 — die Aufnahme wird nie geprueft.
      // Alle vier anderen Uploads im Projekt machen es genauso
      // (`api/resources/speakers.ts`, `useAudioRecording`, `useDocumentUpload`).
      const { data } = await apiClient.post('/api/auth/voice', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      if (data?.success) {
        await onVerified();
        return;
      }
      // 🛑 `data.message` wird NICHT angezeigt. Sie ist absichtlich
      // uninformativ, und sie durchzureichen würde nur Rauschen erzeugen —
      // oder, wenn der Server sie je auskunftsfreudiger macht, ein Orakel in
      // die Oberfläche tragen.
      setRefused(true);
      setAttempts((n) => n + 1);
    } catch {
      setRefused(true);
      setAttempts((n) => n + 1);
    } finally {
      setSubmitting(false);
      rec.reset();
    }
  };

  const deviceError = rec.error;
  const showRecoveryHint = attempts >= HINT_AFTER_ATTEMPTS;
  const progress = Math.min(1, rec.elapsedMs / MAX_DURATION_MS);

  return (
    <div className="card p-6 space-y-5" aria-labelledby="v2f-title">
      <div className="space-y-1">
        <h2 id="v2f-title" className="text-lg font-bold text-gray-900 dark:text-gray-100">
          {t('auth.voiceFactor.title')}
        </h2>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          {t('auth.voiceFactor.intro', { seconds: Math.round(MIN_DURATION_MS / 1000) })}
        </p>
      </div>

      {/* Aussteuerung. Ohne Bewegungswunsch nur ein statischer Balken —
          `motion-reduce` deckt genau das ab (DESIGN.md §Motion). */}
      <div
        className="h-2 w-full rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden"
        role="presentation"
      >
        <div
          className="h-full rounded-full bg-primary-500 transition-[width] duration-100 motion-reduce:transition-none"
          style={{ width: `${(rec.recording ? Math.max(progress, rec.level * 0.15) : progress) * 100}%` }}
        />
      </div>

      {/* Zustand für Screenreader. `polite`, damit es die Eingabe nicht
          unterbricht (DESIGN.md §Accessibility). */}
      <p aria-live="polite" className="sr-only">
        {rec.recording
          ? t('auth.voiceFactor.srRecording')
          : rec.blob
            ? t('auth.voiceFactor.srCaptured')
            : ''}
      </p>

      {/* Die beiden Hinweise tragen dieselbe visuelle Sprache wie
          `.merge-notice` (DESIGN.md: warm = Aufmerksamkeit, Symbol + Text, damit
          Farbe nie das einzige Signal ist) — aber NICHT jene Klasse: sie ist
          dort ausdruecklich als Merge-Hinweis definiert, und DESIGN.md verlangt,
          dass ein Klassenname EIN Konzept traegt. */}
      {deviceError && (
        <div
          className="flex items-start gap-2 rounded-sm px-2 py-1 text-xs font-medium bg-cream text-primary-700 border border-primary-200 dark:bg-gray-800 dark:text-primary-300 dark:border-primary-700"
          role="alert"
        >
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
          <span>{t(`auth.voiceFactor.deviceError.${deviceError}`)}</span>
        </div>
      )}

      {refused && !deviceError && (
        <div className="flex items-start gap-2 rounded-sm px-2 py-1 text-xs font-medium bg-cream text-primary-700 border border-primary-200 dark:bg-gray-800 dark:text-primary-300 dark:border-primary-700" role="alert">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
          <span>
            {t('auth.voiceFactor.refused')}
            {showRecoveryHint && ` ${t('auth.voiceFactor.recoveryHint')}`}
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {!rec.recording && !rec.blob && (
          <button
            ref={recordButtonRef}
            type="button"
            onClick={() => void rec.start()}
            disabled={submitting}
            className="btn-primary inline-flex items-center gap-2 px-4 min-h-11"
          >
            <Mic className="w-4 h-4" aria-hidden="true" />
            {t('auth.voiceFactor.record')}
          </button>
        )}

        {rec.recording && (
          <button
            type="button"
            onClick={rec.stop}
            disabled={!rec.longEnough}
            className="btn-primary inline-flex items-center gap-2 px-4 min-h-11"
            // Vor der Mindestdauer ist der Knopf gesperrt — mit einer Begründung,
            // sonst ist er nur kaputt.
            title={rec.longEnough ? undefined : t('auth.voiceFactor.keepTalking')}
          >
            <Square className="w-4 h-4" aria-hidden="true" />
            {rec.longEnough
              ? t('auth.voiceFactor.stop')
              : t('auth.voiceFactor.keepTalking')}
          </button>
        )}

        {rec.blob && !rec.recording && (
          <>
            <button
              type="button"
              onClick={() => void submit()}
              disabled={submitting}
              className="btn-primary inline-flex items-center gap-2 px-4 min-h-11"
            >
              <Check className="w-4 h-4" aria-hidden="true" />
              {submitting ? t('auth.voiceFactor.checking') : t('auth.voiceFactor.confirm')}
            </button>
            <button
              type="button"
              onClick={rec.reset}
              disabled={submitting}
              className="btn-secondary px-4 min-h-11"
            >
              {t('auth.voiceFactor.again')}
            </button>
          </>
        )}

        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="btn-secondary px-4 min-h-11 ml-auto"
        >
          {t('common.cancel')}
        </button>
      </div>
    </div>
  );
}
