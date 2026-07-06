import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useControlledEnroll, type ControlledEnrollResult } from '../../api/resources/speakers';
import { VOICE_MIC_CONSTRAINTS } from '../../pages/ChatPage/hooks/voiceAudioUtils';
import Modal from '../Modal';

const MIN_SAMPLES = 3;
const RECOMMENDED = 5;

interface Sample {
  blob: Blob;
  seconds: number;
}

interface GuidedEnrollModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Users offered for the voice→account link. */
  users: Array<{ id: number; username: string }>;
  /** Re-enrol an existing speaker (replaces its embeddings) when set. */
  speakerId?: number | null;
  defaultName?: string;
  onEnrolled?: () => void;
}

/**
 * Guided multi-take controlled enrollment (Phase 2, docs/design/speaker-enrollment-redesign.md).
 * Records several clean samples, POSTs them to `/api/speakers/enroll` (voice-server ONNX +
 * cohesion gate); a rejection returns an actionable reason to re-record.
 */
export default function GuidedEnrollModal({
  isOpen, onClose, users, speakerId, defaultName, onEnrolled,
}: GuidedEnrollModalProps) {
  const { t } = useTranslation();
  const enroll = useControlledEnroll();

  const [name, setName] = useState(defaultName ?? '');
  const [userId, setUserId] = useState<number | ''>('');
  const [samples, setSamples] = useState<Sample[]>([]);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<ControlledEnrollResult | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedRef = useRef(0);
  const timerRef = useRef<number | null>(null);

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((tr) => tr.stop());
    streamRef.current = null;
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      setName(defaultName ?? '');
      setUserId('');
      setSamples([]);
      setResult(null);
      setRecording(false);
    }
    return () => stopStream();
  }, [isOpen, defaultName, stopStream]);

  const startRecording = useCallback(async () => {
    setResult(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia(VOICE_MIC_CONSTRAINTS);
      streamRef.current = stream;
      const rec = new MediaRecorder(stream);
      recorderRef.current = rec;
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        const seconds = (Date.now() - startedRef.current) / 1000;
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' });
        stopStream();
        setRecording(false);
        if (blob.size > 0) setSamples((s) => [...s, { blob, seconds }]);
      };
      startedRef.current = Date.now();
      setElapsed(0);
      timerRef.current = window.setInterval(
        () => setElapsed((Date.now() - startedRef.current) / 1000), 200,
      );
      rec.start();
      setRecording(true);
    } catch {
      setResult({ ok: false, reason: t('speakers.micDenied'), accepted: 0 });
    }
  }, [stopStream, t]);

  const stopRecording = useCallback(() => recorderRef.current?.stop(), []);
  const removeSample = (i: number) => setSamples((s) => s.filter((_, idx) => idx !== i));
  const resetForm = () => {
    setName('');
    setUserId('');
    setSamples([]);
    setResult(null);
  };

  const submit = async () => {
    setResult(null);
    const res = await enroll.mutateAsync({
      name: name.trim(),
      samples: samples.map((s) => s.blob),
      userId: userId === '' ? null : Number(userId),
      speakerId: speakerId ?? null,
    });
    setResult(res);
    if (res.ok) onEnrolled?.();
  };

  const canSubmit =
    name.trim().length > 0 && samples.length >= MIN_SAMPLES && !recording && !enroll.isPending;

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('speakers.enrollGuided')}>
      {result?.ok ? (
        <div className="space-y-4">
          <div className="rounded p-4 text-center bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300">
            <div className="text-3xl mb-1">✓</div>
            <div className="font-medium">{t('speakers.enrollDone', { name: result.name ?? name })}</div>
            <div className="text-sm mt-1">
              {t('speakers.enrollOk', {
                cohesion: (result.cohesion ?? 0).toFixed(2), accepted: result.accepted,
              })}
            </div>
            {result.displaced_user_ids && result.displaced_user_ids.length > 0 && (
              <div className="text-xs mt-2 text-amber-600 dark:text-amber-400">
                {t('speakers.enrollDisplaced')}
              </div>
            )}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-secondary" onClick={resetForm}>
              {t('speakers.enrollAnother')}
            </button>
            <button type="button" className="btn-primary" onClick={onClose}>
              {t('speakers.enrollCloseDone')}
            </button>
          </div>
        </div>
      ) : (
      <div className="space-y-4">
        <p className="text-sm text-gray-600 dark:text-gray-400">{t('speakers.enrollGuidedHint')}</p>

        <div>
          <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">
            {t('speakers.enrollName')}
          </label>
          <input
            className="input w-full"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('speakers.enrollNamePlaceholder') ?? ''}
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">
            {t('speakers.linkUser')}
          </label>
          <select
            className="input w-full"
            value={userId}
            onChange={(e) => setUserId(e.target.value === '' ? '' : Number(e.target.value))}
          >
            <option value="">{t('speakers.linkUserNone')}</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.username}</option>
            ))}
          </select>
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('speakers.samplesLabel')}
            </span>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {t('speakers.samplesProgress', {
                count: samples.length, recommended: RECOMMENDED, min: MIN_SAMPLES,
              })}
            </span>
          </div>

          {samples.length > 0 && (
            <ul className="space-y-1 mb-2">
              {samples.map((s, i) => (
                <li
                  key={i}
                  className="flex items-center justify-between text-sm px-3 py-1.5 rounded bg-gray-50 dark:bg-gray-800"
                >
                  <span className="text-gray-700 dark:text-gray-300">
                    {t('speakers.sampleN', { n: i + 1 })} · {s.seconds.toFixed(1)}s
                  </span>
                  <button
                    type="button"
                    className="text-xs text-red-500 hover:underline"
                    onClick={() => removeSample(i)}
                  >
                    {t('speakers.removeSample')}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <button
            type="button"
            className="btn-secondary w-full"
            onClick={recording ? stopRecording : startRecording}
          >
            {recording
              ? `⏹ ${t('speakers.stopRecording')} (${elapsed.toFixed(1)}s)`
              : `🎙️ ${t('speakers.recordSample')}`}
          </button>
        </div>

        {result && !result.ok && (
          <div className="text-sm rounded p-3 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300">
            {result.reason}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            {t('speakers.enrollCancel')}
          </button>
          <button type="button" className="btn-primary" disabled={!canSubmit} onClick={submit}>
            {enroll.isPending ? t('speakers.enrolling') : t('speakers.enrollSubmit')}
          </button>
        </div>
      </div>
      )}
    </Modal>
  );
}
