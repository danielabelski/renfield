import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';

import ReviewBucketSection from '../../../../src/frontend/src/components/speakers/ReviewBucketSection';
import { renderWithProviders } from '../test-utils';

// Mock the review-bucket hooks so no react-query / network is needed.
const promoteAsync = vi.fn();
const dismissAsync = vi.fn();
let bucketData: { total: number; candidates: unknown[] } = { total: 0, candidates: [] };

vi.mock('../../../../src/frontend/src/api/resources/speakers', () => ({
  useReviewBucketQuery: () => ({ data: bucketData, isLoading: false, errorMessage: null }),
  usePromoteCandidates: () => ({ mutateAsync: promoteAsync, isPending: false }),
  useDismissCandidates: () => ({ mutateAsync: dismissAsync, isPending: false }),
}));

const CANDIDATES = [
  { id: 1, best_score: 0.62, nearest_speaker: 'Anna', audio_duration_s: 3.2, created_at: null },
  { id: 2, best_score: null, nearest_speaker: null, audio_duration_s: 2.0, created_at: null },
];

const users = [{ id: 7, username: 'eduard' }];

beforeEach(() => {
  promoteAsync.mockReset();
  dismissAsync.mockReset();
  bucketData = { total: 2, candidates: CANDIDATES };
});

describe('ReviewBucketSection', () => {
  it('self-hides when the bucket is empty', () => {
    bucketData = { total: 0, candidates: [] };
    const { container } = renderWithProviders(<ReviewBucketSection users={users} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('lists candidates with their nearest-profile context', () => {
    renderWithProviders(<ReviewBucketSection users={users} />);
    expect(screen.getByText(/Ähnlichste: Anna/i)).toBeInTheDocument();
    expect(screen.getByText(/Keine Übereinstimmung/i)).toBeInTheDocument();
  });

  it('gates the actions until a candidate is selected, then dismisses the selection', async () => {
    renderWithProviders(<ReviewBucketSection users={users} />);
    const dismissBtn = screen.getByRole('button', { name: /Verwerfen/i });
    expect(dismissBtn).toBeDisabled();

    fireEvent.click(screen.getByLabelText(/Kandidat 1 auswählen/i));
    await waitFor(() => expect(dismissBtn).not.toBeDisabled());

    fireEvent.click(dismissBtn);
    await waitFor(() => expect(dismissAsync).toHaveBeenCalledWith([1]));
  });

  it('promotes selected candidates to a named speaker', async () => {
    promoteAsync.mockResolvedValue({ ok: true, speaker_id: 9, name: 'Anna', accepted: 1 });
    renderWithProviders(<ReviewBucketSection users={users} />);

    fireEvent.click(screen.getByLabelText(/Kandidat 1 auswählen/i));
    fireEvent.click(screen.getByRole('button', { name: /Als Sprecher übernehmen/i }));

    // modal opened
    const nameInput = await screen.findByPlaceholderText(/Max Mustermann/i);
    fireEvent.change(nameInput, { target: { value: 'Anna' } });
    fireEvent.click(screen.getByRole('button', { name: /^Übernehmen$/i }));

    await waitFor(() => expect(promoteAsync).toHaveBeenCalledTimes(1));
    const arg = promoteAsync.mock.calls[0][0];
    expect(arg.candidate_ids).toEqual([1]);
    expect(arg.name).toBe('Anna');
  });
});
