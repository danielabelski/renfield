/**
 * „Mein Konto" — die Einwilligung gehört der Person.
 *
 * 🛑 Bis zum 2026-09-28 stand `require_permission(USERS_MANAGE)` vor beiden
 * Richtungen der Route, und die einzige Oberfläche dafür war die
 * Benutzerverwaltung. Ein Haushaltsmitglied konnte also weder einwilligen noch
 * sehen, ob der Faktor für sein Konto gilt — bei einer Einwilligung nach
 * Art. 9 DSGVO die falsche Eigentumsverteilung.
 */
import { createElement, Fragment } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { BASE_URL } from '../mocks/handlers';
import AccountSettingsPage from '../../../../src/frontend/src/pages/AccountSettingsPage';
import { renderWithProviders } from '../test-utils';
import { useAuth, type AuthContextValue } from '../../../../src/frontend/src/context/AuthContext';
import { adminAuthMock } from '../test-auth-mock';
import type { UseConfirmDialogResult } from '../../../../src/frontend/src/components/ConfirmDialog';

vi.mock('../../../../src/frontend/src/context/AuthContext', async () => {
  const actual = await vi.importActual<typeof import('../../../../src/frontend/src/context/AuthContext')>(
    '../../../../src/frontend/src/context/AuthContext',
  );
  return { ...actual, useAuth: vi.fn<() => AuthContextValue>() };
});

vi.mock('../../../../src/frontend/src/components/ConfirmDialog', () => {
  const result: UseConfirmDialogResult = {
    confirm: () => Promise.resolve(true),
    ConfirmDialogComponent: createElement(Fragment),
  };
  return { useConfirmDialog: (): UseConfirmDialogResult => result };
});

/**
 * Der GRUND kommt seit dem 2026-09-29 vom Server, nicht aus den Feature-Flags.
 *
 * 🛑 Vorher las die Seite nur die instanzweite Hälfte (die Flags) und zeigte
 * einen Grund ausserdem nur im Zustand „scharf". Wer noch nicht eingewilligt
 * hatte, sah ein blankes „Aus" — gemessen im Haushalt bei 6 von 7 Konten, denn
 * deren Blocker war `no_profile`, die kontogebundene Hälfte.
 */
function state(blocker: string | null = null, enabled = false) {
  server.use(
    http.get(`${BASE_URL}/api/users/:id/voice-second-factor`, () =>
      HttpResponse.json({ enabled, blocker }),
    ),
  );
}

function asUser(armed: boolean, fetchUser = vi.fn()) {
  vi.mocked(useAuth).mockReturnValue({
    ...adminAuthMock,
    user: { ...adminAuthMock.user!, voice_second_factor_enabled: armed },
    fetchUser,
  } as AuthContextValue);
}

function captureToggle() {
  const calls: Array<{ id: string; enabled: boolean; current_password?: string }> = [];
  server.use(
    http.post<{ id: string }, { enabled: boolean; current_password?: string | null }>(
      `${BASE_URL}/api/users/:id/voice-second-factor`,
      async ({ params, request }) => {
        const body = await request.json();
        calls.push(
          body.current_password
            ? { id: params.id, enabled: body.enabled, current_password: body.current_password }
            : { id: params.id, enabled: body.enabled },
        );
        return HttpResponse.json({ id: Number(params.id), username: 'x' });
      },
    ),
  );
  return calls;
}

describe('AccountSettingsPage', () => {
  beforeEach(() => { server.resetHandlers(); state(); });
  afterEach(() => vi.clearAllMocks());

  it('zeigt den eigenen Zustand — ohne Verwaltungsrecht', async () => {
    asUser(false);
    renderWithProviders(<AccountSettingsPage />);
    expect(await screen.findByText('Aus — Ihr Passwort genügt')).toBeInTheDocument();
  });

  it('die Person kann fuer sich selbst einwilligen', async () => {
    asUser(false);
    const calls = captureToggle();
    renderWithProviders(<AccountSettingsPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Stimme verlangen' }));
    await waitFor(() => expect(calls).toEqual([{ id: '1', enabled: true }]));
  });

  it('🛑 verlangt zum Zuruecknehmen erst das Passwort', async () => {
    // Der Server verweigert das Abschalten des EIGENEN Faktors ohne Passwort
    // (400): ein Token von VOR der Einwilligung ueberlebt sie und duerfte sie
    // sonst zuruecknehmen. Ohne Feld liefe die Schaltflaeche ins 400.
    asUser(true);
    const calls = captureToggle();
    renderWithProviders(<AccountSettingsPage />);

    await userEvent.click(await screen.findByRole('button', { name: 'Nicht mehr verlangen' }));
    // Erster Klick oeffnet nur das Feld — noch nichts gesendet.
    expect(calls).toEqual([]);
    const feld = await screen.findByLabelText('Passwort zur Bestätigung');
    expect(screen.getByRole('button', { name: 'Nicht mehr verlangen' })).toBeDisabled();

    await userEvent.type(feld, 'geheim');
    await userEvent.click(screen.getByRole('button', { name: 'Nicht mehr verlangen' }));
    await waitFor(() =>
      expect(calls).toEqual([{ id: '1', enabled: false, current_password: 'geheim' }]),
    );
  });

  it('🛑 liest `/auth/me` neu, statt der Antwort der Mutation zu glauben', async () => {
    // Sonst zeigt die Seite etwas anderes als die Sitzung glaubt — und der
    // Zustand dieser Seite IST der Sitzungszustand.
    const fetchUser = vi.fn().mockResolvedValue(null);
    asUser(false, fetchUser);
    captureToggle();
    renderWithProviders(<AccountSettingsPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Stimme verlangen' }));
    await waitFor(() => expect(fetchUser).toHaveBeenCalled());
  });

  it('unterscheidet „scharf\" von „ruht\", wenn der Sprachweg aus ist', async () => {
    asUser(true);
    state('voice_path_off', true);
    renderWithProviders(<AccountSettingsPage />);
    expect(
      await screen.findByText('Eingewilligt, ruht aber (Sprachweg auf dieser Instanz aus)'),
    ).toBeInTheDocument();
  });

  it('🛑 nennt den Grund AUCH ohne Einwilligung', async () => {
    // Der eigentliche Befund vom 2026-09-29: im Zustand „Aus" schwieg die
    // Seite vollstaendig, ganz gleich wie viele Vorbedingungen fehlten.
    asUser(false);
    state('no_profile');
    renderWithProviders(<AccountSettingsPage />);
    expect(await screen.findByText('Aus — Ihr Passwort genügt')).toBeInTheDocument();
    expect(await screen.findByText(/noch kein Sprecherprofil verknüpft/)).toBeInTheDocument();
  });

  it('🛑 bietet kein Einschalten an, das der Server ablehnen MUSS', async () => {
    asUser(false);
    state('no_profile');
    renderWithProviders(<AccountSettingsPage />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Stimme verlangen' })).toBeDisabled(),
    );
  });

  it('🛑 `voice_path_off` blockiert das Einschalten NICHT', async () => {
    // Sonst waere die Reihenfolge des Cutovers nicht durchfuehrbar: erst
    // Einwilligung sammeln, dann das Flag umlegen.
    asUser(false);
    state('voice_path_off');
    renderWithProviders(<AccountSettingsPage />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Stimme verlangen' })).toBeEnabled(),
    );
  });

  it('🛑 uebersetzt den 409-Code, statt ihn roh anzuzeigen', async () => {
    // Der Server schickt seit dem 2026-09-29 den CODE. Ohne Uebersetzung
    // stuende „no_embeddings" in der Oberflaeche — vorher stand dort ein
    // hartkodierter ENGLISCHER Satz.
    asUser(false);
    state(null);
    server.use(
      http.post(`${BASE_URL}/api/users/:id/voice-second-factor`, () =>
        HttpResponse.json({ detail: 'no_embeddings' }, { status: 409 }),
      ),
    );
    renderWithProviders(<AccountSettingsPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Stimme verlangen' }));
    expect(await screen.findByText(/noch keine Stimmprobe/)).toBeInTheDocument();
    expect(screen.queryByText('no_embeddings')).not.toBeInTheDocument();
  });

  it('nennt bei scharfem Faktor, dass es keinen Rueckfall gibt', async () => {
    asUser(true);
    renderWithProviders(<AccountSettingsPage />);
    expect(await screen.findByText(/Rückfall auf das Passwort allein/)).toBeInTheDocument();
  });

  it('beim EINSCHALTEN wird kein Passwort verlangt', async () => {
    // Nur das Entfernen ist der Angriffsweg. Beim Erteilen waere die Eingabe
    // Reibung ohne Gewinn.
    asUser(false);
    const calls = captureToggle();
    renderWithProviders(<AccountSettingsPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Stimme verlangen' }));
    await waitFor(() => expect(calls).toEqual([{ id: '1', enabled: true }]));
    expect(screen.queryByLabelText('Passwort zur Bestätigung')).not.toBeInTheDocument();
  });
});
