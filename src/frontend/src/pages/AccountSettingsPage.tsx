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
import { useFeatureFlags } from '../api/resources/brain';
import { useSetVoiceSecondFactor } from '../api/resources/users';
import { extractApiError } from '../utils/axios';
import Alert from '../components/Alert';
import PageHeader from '../components/PageHeader';

export default function AccountSettingsPage() {
  const { t } = useTranslation();
  const { user, fetchUser } = useAuth();
  const { confirm, ConfirmDialogComponent } = useConfirmDialog();
  const featureFlags = useFeatureFlags();
  const setVoiceSecondFactor = useSetVoiceSecondFactor();

  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const armed = !!user?.voice_second_factor_enabled;

  /**
   * Ruht der Faktor trotz Einwilligung? Spiegelt `restingReason` in der
   * Verwaltung und damit `services/voice_factor_preconditions` im Backend.
   * Hier zählt nur die instanzweite Hälfte — ob das eigene Sprecherprofil
   * taugt, weiss diese Seite nicht (die Sprecherliste ist Verwaltungsstoff).
   * Solange die Schalter laden, wird NICHT auf „ruht" geschlossen: eine
   * unbekannte Antwort ist kein Beweis.
   */
  const flags = featureFlags.data;
  const pathOff =
    flags?.voice_auth_enabled === false || flags?.speaker_recognition_enabled === false;

  const handleToggle = async () => {
    if (!user) return;
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
      await setVoiceSecondFactor.mutateAsync({ id: user.id, enabled: enabling });
      // `/auth/me` neu lesen: der Zustand dieser Seite kommt von dort, nicht aus
      // der Antwort der Mutation — sonst zeigt die Seite etwas anderes als die
      // Sitzung glaubt.
      await fetchUser();
      setSuccess(enabling ? t('account.voiceArmed') : t('account.voiceDisarmed'));
    } catch (err) {
      setError(extractApiError(err, t('account.voiceFailed')));
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
                armed && !pathOff
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
                : pathOff
                  ? t('account.voiceStateResting')
                  : t('account.voiceStateOn')}
            </p>

            {armed && (
              <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                {t('account.voiceNoFallback')}
              </p>
            )}
          </div>

          <button
            onClick={handleToggle}
            disabled={setVoiceSecondFactor.isPending || !user}
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
