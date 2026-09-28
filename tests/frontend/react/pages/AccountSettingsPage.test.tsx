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

function flags(over: { voice_auth_enabled?: boolean; speaker_recognition_enabled?: boolean } = {}) {
  server.use(
    http.get(`${BASE_URL}/api/config/features`, () =>
      HttpResponse.json({ voice_auth_enabled: true, speaker_recognition_enabled: true, ...over }),
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
  const calls: Array<{ id: string; enabled: boolean }> = [];
  server.use(
    http.post<{ id: string }, { enabled: boolean }>(
      `${BASE_URL}/api/users/:id/voice-second-factor`,
      async ({ params, request }) => {
        calls.push({ id: params.id, enabled: (await request.json()).enabled });
        return HttpResponse.json({ id: Number(params.id), username: 'x' });
      },
    ),
  );
  return calls;
}

describe('AccountSettingsPage', () => {
  beforeEach(() => { server.resetHandlers(); flags(); });
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

  it('und sie wieder zuruecknehmen', async () => {
    asUser(true);
    const calls = captureToggle();
    renderWithProviders(<AccountSettingsPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Nicht mehr verlangen' }));
    await waitFor(() => expect(calls).toEqual([{ id: '1', enabled: false }]));
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
    flags({ voice_auth_enabled: false });
    renderWithProviders(<AccountSettingsPage />);
    expect(
      await screen.findByText('Eingewilligt, ruht aber (Sprachweg auf dieser Instanz aus)'),
    ).toBeInTheDocument();
  });

  it('nennt bei scharfem Faktor, dass es keinen Rueckfall gibt', async () => {
    asUser(true);
    renderWithProviders(<AccountSettingsPage />);
    expect(await screen.findByText(/Rückfall auf das Passwort allein/)).toBeInTheDocument();
  });
});
