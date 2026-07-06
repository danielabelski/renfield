import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  useReviewBucketQuery,
  usePromoteCandidates,
  useDismissCandidates,
  type ControlledEnrollResult,
} from '../../api/resources/speakers';
import Modal from '../Modal';

interface ReviewBucketSectionProps {
  /** Users offered for the voice→account link on promote. */
  users: Array<{ id: number; username: string }>;
}

/**
 * Review bucket (Phase 3b, docs/design/speaker-enrollment-redesign.md). Under
 * controlled recognition an unmatched unknown voice is captured instead of
 * auto-enrolled a polluting "Unbekannter Sprecher"; the admin selects the ones
 * that belong to one person and promotes them to a named enrolled speaker, or
 * dismisses noise. Self-hiding: renders nothing until the bucket has candidates.
 */
export default function ReviewBucketSection({ users }: ReviewBucketSectionProps) {
  const { t } = useTranslation();
  const bucket = useReviewBucketQuery();
  const promote = usePromoteCandidates();
  const dismiss = useDismissCandidates();

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [name, setName] = useState('');
  const [userId, setUserId] = useState<number | ''>('');
  const [result, setResult] = useState<ControlledEnrollResult | null>(null);

  const candidates = bucket.data?.candidates ?? [];
  const total = bucket.data?.total ?? 0;
  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  // Self-hiding: render nothing while loading OR when the bucket is empty (the
  // flag is dark by default, so the empty case is the norm — hiding during the
  // fetch too avoids a "Review queue" card flashing on every Speakers-page load).
  // An error still shows so a broken endpoint isn't swallowed.
  if (!bucket.errorMessage && (bucket.isLoading || total === 0)) return null;

  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const openPromote = () => {
    setName('');
    setUserId('');
    setResult(null);
    setPromoteOpen(true);
  };

  const submitPromote = async () => {
    setResult(null);
    try {
      const res = await promote.mutateAsync({
        candidate_ids: selectedIds,
        name: name.trim(),
        user_id: userId === '' ? null : Number(userId),
      });
      setResult(res);
      if (res.ok) {
        setSelected(new Set());
        setPromoteOpen(false);
      }
    } catch {
      // HTTP/network failure — surfaced via promote.errorMessage in the modal.
    }
  };

  const dismissSelected = async () => {
    try {
      await dismiss.mutateAsync(selectedIds);
      setSelected(new Set());
    } catch {
      // HTTP/network failure — surfaced via dismiss.errorMessage below.
    }
  };

  const canPromote = name.trim().length > 0 && selectedIds.length > 0 && !promote.isPending;

  return (
    <section className="card">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            {t('speakers.reviewBucketTitle')}
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {t('speakers.reviewBucketHint')}
          </p>
        </div>
        <span className="text-sm text-gray-500 dark:text-gray-400">
          {t('speakers.reviewBucketCount', { count: total })}
        </span>
      </div>

      {bucket.isLoading ? (
        <div className="py-6 text-center text-gray-500 dark:text-gray-400" role="status">
          {t('common.loading')}
        </div>
      ) : bucket.errorMessage ? (
        <div className="text-sm rounded p-3 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300">
          {bucket.errorMessage}
        </div>
      ) : (
        <>
          <ul className="space-y-1">
            {candidates.map((c) => (
              <li
                key={c.id}
                className="flex items-center gap-3 text-sm px-3 py-2 rounded bg-gray-50 dark:bg-gray-800"
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 shrink-0"
                  checked={selected.has(c.id)}
                  onChange={() => toggle(c.id)}
                  aria-label={t('speakers.reviewSelect', { id: c.id }) ?? undefined}
                />
                <span className="flex-1 text-gray-700 dark:text-gray-300">
                  {c.nearest_speaker
                    ? t('speakers.reviewNearest', {
                        name: c.nearest_speaker,
                        score: ((c.best_score ?? 0) * 100).toFixed(0),
                      })
                    : t('speakers.reviewNoMatch')}
                </span>
                {c.audio_duration_s != null && (
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    {c.audio_duration_s.toFixed(1)}s
                  </span>
                )}
              </li>
            ))}
          </ul>

          {dismiss.errorMessage && (
            <div className="text-sm rounded p-3 mt-2 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300">
              {dismiss.errorMessage}
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-3">
            <button
              type="button"
              className="btn-secondary"
              disabled={selectedIds.length === 0 || dismiss.isPending}
              onClick={dismissSelected}
            >
              {t('speakers.reviewDismiss', { count: selectedIds.length })}
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={selectedIds.length === 0}
              onClick={openPromote}
            >
              {t('speakers.reviewPromote', { count: selectedIds.length })}
            </button>
          </div>
        </>
      )}

      <Modal
        isOpen={promoteOpen}
        onClose={() => setPromoteOpen(false)}
        title={t('speakers.reviewPromoteTitle')}
      >
        <div className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {t('speakers.reviewPromoteHint', { count: selectedIds.length })}
          </p>

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

          {(result && !result.ok) || promote.errorMessage ? (
            <div className="text-sm rounded p-3 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300">
              {result && !result.ok ? result.reason : promote.errorMessage}
            </div>
          ) : null}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-secondary" onClick={() => setPromoteOpen(false)}>
              {t('speakers.enrollCancel')}
            </button>
            <button type="button" className="btn-primary" disabled={!canPromote} onClick={submitPromote}>
              {promote.isPending ? t('speakers.enrolling') : t('speakers.reviewPromoteSubmit')}
            </button>
          </div>
        </div>
      </Modal>
    </section>
  );
}
