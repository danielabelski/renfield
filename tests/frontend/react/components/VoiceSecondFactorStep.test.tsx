/**
 * Die Erfassungsmaske des zweiten Faktors.
 *
 * 🛑 Die wichtigsten zwei Tests hier prüfen, was die Maske NICHT tut:
 *
 * * `does not surface the server message` — der Server antwortet auf jeden
 *   Fehlschlag identisch und absichtlich uninformativ. Reicht die Oberfläche
 *   diese Meldung durch, trägt sie ein Orakel nach draußen, sobald der Server je
 *   auskunftsfreudiger wird. Der Schutz muss auf BEIDEN Seiten stehen.
 * * `offers no way past the factor` — es darf keinen „ohne Stimme anmelden"-Knopf
 *   geben, auch nicht nach mehreren Fehlversuchen. Ein Rückfall, den ein
 *   Angreifer selbst auslöst, hebt den Faktor auf.
 *
 * Mikrofonfehler sind dagegen ABSICHTLICH unterscheidbar: es geht um das Gerät
 * des Nutzers, nicht um ein Geheimnis des Servers.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';

// 🛑 Mit dem ECHTEN Sprachpaket, nicht mit einem Stub. Sonst liefert `t()` die
// Schlüssel zurück, jede Textsuche prüft nur Schlüsselnamen, und ein fehlender
// Übersetzungseintrag fällt nie auf — genau die Lücke, die ein Test hier
// schließen soll.
import i18n from '../../../../src/frontend/src/i18n';

import VoiceSecondFactorStep from '../../../../src/frontend/src/components/auth/VoiceSecondFactorStep';
import apiClient from '../../../../src/frontend/src/utils/axios';

vi.mock('../../../../src/frontend/src/utils/axios', () => ({
  default: { post: vi.fn() },
}));

/** Ein MediaRecorder-Ersatz, den wir von Hand takten. */
class FakeRecorder {
  static instances: FakeRecorder[] = [];
  state: 'inactive' | 'recording' = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(public stream: unknown) {
    FakeRecorder.instances.push(this);
  }

  start() {
    this.state = 'recording';
  }

  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob(['audio'], { type: 'audio/webm' }) });
    this.onstop?.();
  }
}

function stubMedia({ fail }: { fail?: string } = {}) {
  const track = { stop: vi.fn() };
  const getUserMedia = fail
    ? vi.fn().mockRejectedValue(Object.assign(new Error('nope'), { name: fail }))
    : vi.fn().mockResolvedValue({ getTracks: () => [track] });
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia }, configurable: true, writable: true,
  });
  (globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder;
  return { getUserMedia, track };
}

/** Aufnahme starten, die Mindestdauer überspringen, stoppen. */
async function recordSomething() {
  fireEvent.click(screen.getByRole('button', { name: /aufnahme starten|start recording/i }));
  await waitFor(() => expect(FakeRecorder.instances.length).toBeGreaterThan(0));
  // Die Mindestdauer wird über die Uhr geprüft, nicht über Timer — vorspulen.
  vi.setSystemTime(Date.now() + 5000);
  FakeRecorder.instances.at(-1)!.stop();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /bestätigen|confirm/i })).toBeInTheDocument()
  );
}

/** Rendern mit dem echten Sprachpaket auf Deutsch. */
function renderStep(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe('VoiceSecondFactorStep', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
    FakeRecorder.instances = [];
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(apiClient.post).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('sends the ticket and the recording to /api/auth/voice', async () => {
    stubMedia();
    vi.mocked(apiClient.post).mockResolvedValue({ data: { success: true } });
    const onVerified = vi.fn();

    renderStep(
      <VoiceSecondFactorStep ticket="TICKET-9" onVerified={onVerified} onCancel={vi.fn()} />
    );
    await recordSomething();
    fireEvent.click(screen.getByRole('button', { name: /bestätigen|confirm/i }));

    await waitFor(() => expect(apiClient.post).toHaveBeenCalled());
    const [url, form] = vi.mocked(apiClient.post).mock.calls[0];
    expect(url).toBe('/api/auth/voice');
    expect((form as FormData).get('ticket')).toBe('TICKET-9');
    expect((form as FormData).get('audio_file')).toBeInstanceOf(Blob);
    await waitFor(() => expect(onVerified).toHaveBeenCalled());
  });

  it('does not surface the server message on a refusal', async () => {
    stubMedia();
    // 🛑 Der Server sendet absichtlich denselben nichtssagenden Text auf jedem
    // Fehlschlag. Zeigt die Oberfläche ihn, trägt sie das Orakel nach draußen,
    // sobald der Server je auskunftsfreudiger wird.
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { success: false, message: 'Voice authentication failed' },
    });

    renderStep(
      <VoiceSecondFactorStep ticket="T" onVerified={vi.fn()} onCancel={vi.fn()} />
    );
    await recordSomething();
    fireEvent.click(screen.getByRole('button', { name: /bestätigen|confirm/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.queryByText(/Voice authentication failed/)).toBeNull();
  });

  it('offers no way past the factor, even after repeated failures', async () => {
    stubMedia();
    vi.mocked(apiClient.post).mockResolvedValue({ data: { success: false } });
    const onVerified = vi.fn();

    renderStep(
      <VoiceSecondFactorStep ticket="T" onVerified={onVerified} onCancel={vi.fn()} />
    );

    for (let i = 0; i < 4; i += 1) {
      await recordSomething();
      fireEvent.click(screen.getByRole('button', { name: /bestätigen|confirm/i }));
      await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    }

    // Nach vier Fehlversuchen: kein Knopf, der die Stimme überspringt.
    const labels = screen.getAllByRole('button').map((b) => b.textContent ?? '');
    expect(labels.join(' ')).not.toMatch(/ohne|skip|überspringen|without/i);
    expect(onVerified).not.toHaveBeenCalled();
  });

  it('names the recovery path after three failures', async () => {
    stubMedia();
    vi.mocked(apiClient.post).mockResolvedValue({ data: { success: false } });

    renderStep(
      <VoiceSecondFactorStep ticket="T" onVerified={vi.fn()} onCancel={vi.fn()} />
    );
    for (let i = 0; i < 3; i += 1) {
      await recordSomething();
      fireEvent.click(screen.getByRole('button', { name: /bestätigen|confirm/i }));
      await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    }
    // Bei einem defekten Mikrofon soll der Nutzer erfahren, wie es weitergeht —
    // ohne dass ihm die Maske selbst einen Weg vorbei anbietet.
    expect(screen.getByRole('alert').textContent).toMatch(/administrativ|administrator/i);
  });

  it('distinguishes a denied microphone — that is the user device, not a secret', async () => {
    stubMedia({ fail: 'NotAllowedError' });

    renderStep(
      <VoiceSecondFactorStep ticket="T" onVerified={vi.fn()} onCancel={vi.fn()} />
    );
    fireEvent.click(screen.getByRole('button', { name: /aufnahme starten|start recording/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toMatch(/mikrofon|microphone/i);
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('refuses to stop before the minimum duration', async () => {
    stubMedia();
    renderStep(
      <VoiceSecondFactorStep ticket="T" onVerified={vi.fn()} onCancel={vi.fn()} />
    );
    fireEvent.click(screen.getByRole('button', { name: /aufnahme starten|start recording/i }));

    // Sofort danach: der Stopp-Knopf ist gesperrt und sagt, warum. Eine halbe
    // Sekunde Audio ergibt einen schlechten Vergleich, und der Nutzer soll das
    // vor dem Absenden merken statt eine Ablehnung zu kassieren.
    await waitFor(() => {
      const stop = screen.getByRole('button', { name: /weitersprechen|keep talking/i });
      expect(stop).toBeDisabled();
    });
  });

  it('releases the microphone when it unmounts', async () => {
    const { track } = stubMedia();
    const { unmount } = renderStep(
      <VoiceSecondFactorStep ticket="T" onVerified={vi.fn()} onCancel={vi.fn()} />
    );
    fireEvent.click(screen.getByRole('button', { name: /aufnahme starten|start recording/i }));
    await waitFor(() => expect(FakeRecorder.instances.length).toBeGreaterThan(0));

    unmount();
    // 🛑 Auf einer ANMELDESEITE ist ein heimlich offenes Mikrofon das Letzte, was
    // man will — die Browser-Anzeige bliebe an.
    expect(track.stop).toHaveBeenCalled();
  });

  it('cancel hands control back without calling the API', () => {
    stubMedia();
    const onCancel = vi.fn();
    renderStep(
      <VoiceSecondFactorStep ticket="T" onVerified={vi.fn()} onCancel={onCancel} />
    );
    fireEvent.click(screen.getByRole('button', { name: /abbrechen|cancel/i }));
    expect(onCancel).toHaveBeenCalled();
    expect(apiClient.post).not.toHaveBeenCalled();
  });
});
