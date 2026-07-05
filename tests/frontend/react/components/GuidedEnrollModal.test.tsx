import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';

import GuidedEnrollModal from '../../../../src/frontend/src/components/speakers/GuidedEnrollModal';
import { renderWithRouter } from '../test-utils';

// Mock the controlled-enroll hook so no react-query / network is needed.
const mutateAsync = vi.fn();
vi.mock('../../../../src/frontend/src/api/resources/speakers', () => ({
  useControlledEnroll: () => ({ mutateAsync, isPending: false }),
}));

// Minimal MediaRecorder + getUserMedia so a "record → stop" toggle yields a sample.
class MockRecorder {
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  mimeType = 'audio/webm';
  start() {}
  stop() {
    this.ondataavailable?.({ data: new Blob(['x'], { type: 'audio/webm' }) });
    this.onstop?.();
  }
}

beforeEach(() => {
  mutateAsync.mockReset();
  // @ts-expect-error test shim
  global.MediaRecorder = MockRecorder;
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
  });
});

async function recordOnce() {
  const btn = screen.getByRole('button', { name: /record sample|probe aufnehmen/i });
  fireEvent.click(btn); // start (async getUserMedia)
  await waitFor(() =>
    screen.getByRole('button', { name: /stop recording|aufnahme beenden/i }));
  fireEvent.click(screen.getByRole('button', { name: /stop recording|aufnahme beenden/i }));
  await waitFor(() =>
    screen.getByRole('button', { name: /record sample|probe aufnehmen/i }));
}

describe('GuidedEnrollModal', () => {
  const props = { isOpen: true, onClose: vi.fn(), users: [{ id: 1, username: 'eduard' }] };

  it('gates submit on a name and >= 3 samples, then submits the collected blobs', async () => {
    mutateAsync.mockResolvedValue({ ok: true, speaker_id: 5, cohesion: 0.72, accepted: 3 });
    renderWithRouter(<GuidedEnrollModal {...props} />);

    const submit = screen.getByRole('button', { name: /^enroll$|einlernen$/i });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/Max Mustermann/i), {
      target: { value: 'Eduard' },
    });
    expect(submit).toBeDisabled(); // name but no samples

    await recordOnce();
    await recordOnce();
    expect(submit).toBeDisabled(); // only 2 samples
    await recordOnce();
    await waitFor(() => expect(submit).not.toBeDisabled());

    fireEvent.click(submit);
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const arg = mutateAsync.mock.calls[0][0];
    expect(arg.name).toBe('Eduard');
    expect(arg.samples).toHaveLength(3);
  });

  it('shows the rejection reason when the samples do not cohere', async () => {
    mutateAsync.mockResolvedValue({
      ok: false, reason: "samples don't cohere (mean cosine 0.20 < 0.5)", accepted: 3,
    });
    renderWithRouter(<GuidedEnrollModal {...props} />);
    fireEvent.change(screen.getByPlaceholderText(/Max Mustermann/i), {
      target: { value: 'Eduard' },
    });
    await recordOnce();
    await recordOnce();
    await recordOnce();
    fireEvent.click(screen.getByRole('button', { name: /^enroll$|einlernen$/i }));
    await waitFor(() => screen.getByText(/don't cohere/i));
  });
});
