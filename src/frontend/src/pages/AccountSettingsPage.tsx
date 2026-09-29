/**
 * Mein Konto — die Einstellungen, die der Person selbst gehören.
 *
 * 🛑 WARUM ES DIESE SEITE GIBT
 * Die Einwilligung in die Stimme als zweiten Anmeldefaktor ist eine Einwilligung
 * in die Verarbeitung biometrischer Daten (Art. 9 DSGVO). Bis zum 2026-09-28 war
 * sie ausschliesslich über die Benutzerverwaltung erreichbar, also nur für Konten
 * mit `users.manage` — ein Haushaltsmitglied konnte weder einwilligen noch auch
 * nur sehen, ob der Faktor für sein Konto gilt. Eine Einwilligung, die nur ein
 * Dritter erteilen kann, ist keine.
 *
 * Die Asymmetrie bleibt: einschalten nur für sich selbst (das ist hier), ein
 * FREMDES Konto abschalten verlangt weiterhin `admin` und geschieht in der
 * Verwaltung.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldCheck, Shield, Mic, Loader, UserCircle } from 'lucide-react';

import { useAuth } from '../context/AuthContext';
import { useConfirmDialog } from '../components/ConfirmDialog';
import {
  isVoiceFactorBlocker,
  useSetVoiceSecondFactor,
  useVoiceSecondFactorState,
} from '../api/resources/users';
import { extractApiError } from '../utils/axios';
import Alert from '../components/Alert';
import PageHeader from '../components/PageHeader';

export default function AccountSettingsPage() {
  const { t } = useTranslation();
  const { user, fetchUser } = useAuth();
  const { confirm, ConfirmDialogComponent } = useConfirmDialog();
  const voiceState = useVoiceSecondFactorState(user?.id);
  const setVoiceSecondFactor = useSetVoiceSecondFactor();

  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  // 🛑 Nur fürs Abschalten. Der Server verlangt das Passwort dort, weil ein
  // Token von VOR der Einwilligung sie sonst zurücknehmen könnte.
  const [password, setPassword] = useState('');
  const [askPassword, setAskPassword] = useState(false);

  const armed = !!user?.voice_second_factor_enabled;

  /**
   * 🛑 DER GRUND KOMMT VOM SERVER, UND ER GILT UNABHÄNGIG VON DER EINWILLIGUNG.
   *
   * Vorher las diese Seite nur die Feature-Flags — also die INSTANZWEITE Hälfte
   * der Vorbedingungen — und zeigte einen Grund ausserdem nur im Zustand
   * „scharf". Wer noch nicht eingewilligt hatte, sah ein blankes „Aus", ganz
   * gleich wie viele Vorbedingungen fehlten, und bekam eine Schaltfläche
   * angeboten, die mit 409 fehlschlagen musste. Gemessen im Haushalt am
   * 2026-09-29: bei 6 von 7 Konten war `no_profile` der Blocker — genau die
   * kontogebundene Hälfte, die diese Seite nicht sehen konnte.
   *
   * Solange die Antwort aussteht, wird NICHTS geschlossen: eine unbekannte
   * Antwort ist kein Beweis, weder für noch gegen einen Blocker.
   */
  const blocker = voiceState.data?.blocker ?? null;
  const blockerText = blocker ? t(`account.voiceBlocker.${blocker}`) : null;

  /**
   * Was das EINSCHALTEN verhindert — nicht dasselbe wie „etwas steht im Weg".
   * `voice_path_off` blockiert es bewusst NICHT: die Reihenfolge des Cutovers
   * ist erst Einwilligung sammeln, dann das Flag umlegen. Der Server sieht das
   * genauso (`POST …/voice-second-factor` prüft `voice_auth_enabled` nicht).
   */
  const armingBlocked = blocker !== null && blocker !== 'voice_path_off';

  const handleToggle = async () => {
    if (!user) return;
    // Abschalten: erst das Passwort erfragen, dann bestätigen lassen.
    if (armed && !askPassword) {
      setAskPassword(true);
      return;
    }
    const enabling = !armed;
    const confirmed = await confirm({
      title: enabling ? t('account.voiceArm') : t('account.voiceDisarm'),
      message: enabling ? t('account.voiceArmConfirm') : t('account.voiceDisarmConfirmSelf'),
      confirmLabel: enabling ? t('account.voiceArm') : t('account.voiceDisarm'),
      variant: 'warning',
    });
    if (!confirmed) return;

    setError(null);
    setSuccess(null);
    try {
      await setVoiceSecondFactor.mutateAsync({
        id: user.id,
        enabled: enabling,
        currentPassword: enabling ? undefined : password,
      });
      setPassword('');
      setAskPassword(false);
      // `/auth/me` neu lesen: der Zustand dieser Seite kommt von dort, nicht aus
      // der Antwort der Mutation — sonst zeigt die Seite etwas anderes als die
      // Sitzung glaubt.
      await fetchUser();
      setSuccess(enabling ? t('account.voiceArmed') : t('account.voiceDisarmed'));
    } catch (err) {
      // 🛑 Die 409-Antwort trägt den CODE, nicht den Satz — sonst stünde hier
      // roher Text wie „no_profile" in der Oberfläche. Übersetzt wird er hier,
      // aus demselben Vokabular wie der Zustand oben.
      const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      setError(
        isVoiceFactorBlocker(detail)
          ? t(`account.voiceBlocker.${detail}`)
          : extractApiError(err, t('account.voiceFailed')),
      );
    }
  };

  return (
    <div className="p-6">
      <PageHeader icon={UserCircle} title={t('account.title')} subtitle={t('account.subtitle')} />

      {error && <Alert variant="error" onClose={() => setError(null)}>{error}</Alert>}
      {success && <Alert variant="success" onClose={() => setSuccess(null)}>{success}</Alert>}

      <div className="card mt-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <h2 className="flex items-center gap-2 text-lg font-medium text-gray-900 dark:text-white">
              <Mic className="w-5 h-5" aria-hidden="true" />
              {t('account.voiceTitle')}
            </h2>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
              {t('account.voiceExplain')}
            </p>

            {/* Symbol UND Text — die Farbe ist nie das einzige Signal (DESIGN.md). */}
            <p
              className={`mt-3 flex items-center gap-2 text-sm font-medium ${
                armed && !blocker
                  ? 'text-amber-600 dark:text-amber-400'
                  : 'text-gray-500 dark:text-gray-400'
              }`}
              aria-live="polite"
            >
              {armed ? (
                <ShieldCheck className="w-4 h-4" aria-hidden="true" />
              ) : (
                <Shield className="w-4 h-4" aria-hidden="true" />
              )}
              {!armed
                ? t('account.voiceStateOff')
                : blocker
                  ? t('account.voiceStateResting')
                  : t('account.voiceStateOn')}
            </p>

            {/* 🛑 Der GRUND, und zwar in BEIDEN Zuständen. Er ist der einzige
                Hinweis darauf, warum ein Einschalten fehlschlagen würde — vorher
                erfuhr ihn die Person erst nach dem Drücken, und auf Englisch. */}
            {blockerText && (
              <p className="mt-2 text-xs text-gray-600 dark:text-gray-300">
                {blockerText}
              </p>
            )}

            {armed && (
              <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                {t('account.voiceNoFallback')}
              </p>
            )}

            {askPassword && (
              <div className="mt-4">
                <label
                  htmlFor="voice-disarm-password"
                  className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
                >
                  {t('account.voicePasswordLabel')}
                </label>
                <input
                  id="voice-disarm-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="input w-full max-w-sm min-h-11"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  {t('account.voicePasswordWhy')}
                </p>
              </div>
            )}
          </div>

          <button
            onClick={handleToggle}
            disabled={
              setVoiceSecondFactor.isPending
              || !user
              || (askPassword && !password)
              // Einschalten, das der Server mit 409 ablehnen MUSS, wird gar
              // nicht erst angeboten. Abschalten bleibt immer möglich — das ist
              // der dokumentierte Rückweg und darf an keiner Vorbedingung
              // hängen (es gibt bewusst keinen Rückfall auf Passwort allein).
              || (!armed && armingBlocked)
            }
            title={!armed && armingBlocked && blockerText ? blockerText : undefined}
            className={armed ? 'btn-secondary min-h-11' : 'btn-primary min-h-11'}
          >
            {setVoiceSecondFactor.isPending && (
              <Loader className="w-4 h-4 mr-2 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            )}
            {armed ? t('account.voiceDisarm') : t('account.voiceArm')}
          </button>
        </div>
      </div>

      {ConfirmDialogComponent}
    </div>
  );
}
