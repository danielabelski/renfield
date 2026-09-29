/**
 * Die Stimme als zweiten Anmeldefaktor schalten — aus der Nutzerverwaltung.
 *
 * 🛑 Diese Oberfläche ist nicht Beiwerk. `.claude/rules/auth.md` beschreibt als
 * einzigen Weg zurück aus einem defekten Mikrofon: „ein Administrator schaltet
 * `voice_second_factor_enabled` ab". Ohne Schaltfläche wäre dieser Weg eine
 * Behauptung, und das einzige Mittel ein UPDATE von Hand gegen die
 * Produktionsdatenbank.
 *
 * Getestet wird vor allem die ASYMMETRIE: einschalten nur für sich selbst
 * (ein Stimmabdruck ist biometrisches Datum, Art. 9 DSGVO — einwilligen kann
 * niemand für jemanden anderen), abschalten für jedes Konto.
 */
import { createElement, Fragment } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { BASE_URL } from '../mocks/handlers';
import UsersPage from '../../../../src/frontend/src/pages/UsersPage';
import { renderWithProviders } from '../test-utils';
import { useAuth, type AuthContextValue } from '../../../../src/frontend/src/context/AuthContext';
import { adminAuthMock } from '../test-auth-mock';
import type { ModalProps } from '../../../../src/frontend/src/components/Modal';
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

vi.mock('../../../../src/frontend/src/components/Modal', () => ({
  default: ({ isOpen, title, children }: ModalProps) =>
    isOpen ? <div data-testid="modal"><h2>{title}</h2>{children}</div> : null,
}));

interface Row {
  id: number;
  username: string;
  email: string | null;
  role_id: number;
  role_name: string;
  is_active: boolean;
  speaker_id: number | null;
  last_login: string | null;
  created_at: string;
  voice_second_factor_enabled?: boolean;
}

/** `adminAuthMock` meldet sich als `id: 1` an — das ist „das eigene Konto". */
const SELF = 1;

function listing(
  rows: Row[],
  speakers: Array<{ id: number; name: string; embedding_count: number }> = [
    { id: 7, name: 'stimme', embedding_count: 3 },
  ],
  flags: { voice_auth_enabled?: boolean; speaker_recognition_enabled?: boolean } = {},
) {
  server.use(
    http.get(`${BASE_URL}/api/users`, () =>
      HttpResponse.json({ users: rows, total: rows.length, page: 1, page_size: 50 }),
    ),
    http.get(`${BASE_URL}/api/speakers`, () => HttpResponse.json(speakers)),
    // Die instanzweite Haelfte der Vorbedingungen. Standard: Sprachweg AN, damit
    // die uebrigen Faelle die Profil-Haelfte pruefen.
    http.get(`${BASE_URL}/api/config/features`, () =>
      HttpResponse.json({
        voice_auth_enabled: true,
        speaker_recognition_enabled: true,
        ...flags,
      }),
    ),
  );
}

function row(over: Partial<Row> & { id: number; username: string }): Row {
  return {
    email: null,
    role_id: 2,
    role_name: 'User',
    is_active: true,
    speaker_id: 7,
    last_login: null,
    created_at: '2024-01-01T00:00:00Z',
    ...over,
  };
}

/** Fängt den POST ab und gibt zurück, was an den Server ging. */
function captureToggle(): { calls: Array<{ id: string; enabled: boolean }> } {
  const calls: Array<{ id: string; enabled: boolean }> = [];
  server.use(
    http.post<{ id: string }, { enabled: boolean }>(
      `${BASE_URL}/api/users/:id/voice-second-factor`,
      async ({ params, request }) => {
        const body = await request.json();
        calls.push({ id: params.id, enabled: body.enabled });
        return HttpResponse.json({ id: Number(params.id), username: 'x' });
      },
    ),
  );
  return { calls };
}

describe('UsersPage — Stimme als zweiter Faktor', () => {
  beforeEach(() => {
    server.resetHandlers();
    vi.mocked(useAuth).mockReturnValue(adminAuthMock);
  });
  afterEach(() => vi.clearAllMocks());

  it('🛑 übersetzt die Gerätekonto-Absage, statt den Code roh zu zeigen', async () => {
    // Der EINZIGE Ort, an dem `device_account` auftritt: ein Gerätekonto meldet
    // sich nirgends selbst an, die Absage ist also nur von hier erreichbar.
    // Beim Umbau vom 2026-09-29 blieb sie als hartkodierter ENGLISCHER Satz
    // stehen — vier Zeilen neben den dreien, die ersetzt wurden.
    listing([row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1 })]);
    server.use(
      http.post(`${BASE_URL}/api/users/:id/voice-second-factor`, () =>
        HttpResponse.json({ detail: 'device_account' }, { status: 409 }),
      ),
    );

    renderWithProviders(<UsersPage />);
    await userEvent.click(await screen.findByTitle('Stimme als zweiten Faktor einschalten'));

    expect(await screen.findByText(/Gerätekonto, kein Mensch/)).toBeInTheDocument();
    expect(screen.queryByText('device_account')).not.toBeInTheDocument();
    expect(screen.queryByText(/device account has no voice/)).not.toBeInTheDocument();
  });

  it('bietet das Einschalten für das eigene Konto an und meldet es dem Server', async () => {
    listing([row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1 })]);
    const { calls } = captureToggle();

    renderWithProviders(<UsersPage />);
    const button = await screen.findByTitle('Stimme als zweiten Faktor einschalten');
    await userEvent.click(button);

    await waitFor(() => expect(calls).toEqual([{ id: String(SELF), enabled: true }]));
  });

  it('🛑 bietet es für ein FREMDES Konto gar nicht erst an', async () => {
    // Das ist der Kern: eine Administratorin darf eine biometrische Erfassung
    // nicht auferlegen. Die Verweigerung steht im Backend (403), aber eine
    // Schaltfläche, die nur 403 erzeugt, wäre eine Falle.
    listing([
      row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1 }),
      row({ id: 2, username: 'anna' }),
    ]);

    renderWithProviders(<UsersPage />);
    await screen.findByText('anna');
    expect(screen.getAllByTitle('Stimme als zweiten Faktor einschalten')).toHaveLength(1);
  });

  it('bietet das ABSCHALTEN auch für ein fremdes Konto an — der Rückweg', async () => {
    listing([
      row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1 }),
      row({ id: 2, username: 'anna', voice_second_factor_enabled: true }),
    ]);
    const { calls } = captureToggle();

    renderWithProviders(<UsersPage />);
    const button = await screen.findByTitle('Stimme als zweiten Faktor abschalten');
    await userEvent.click(button);

    await waitFor(() => expect(calls).toEqual([{ id: '2', enabled: false }]));
  });

  it('zeigt am fremden Konto an, dass der zweite Faktor scharf ist', async () => {
    listing([row({ id: 2, username: 'anna', voice_second_factor_enabled: true })]);
    renderWithProviders(<UsersPage />);
    expect(await screen.findByText('Stimme als 2. Faktor')).toBeInTheDocument();
  });

  it('sperrt das Einschalten, solange kein Sprecherprofil verknüpft ist', async () => {
    // Einschalten ohne Profil wäre eine Selbstaussperrung: `POST /auth/voice`
    // prüft 1:1 dagegen und verweigert fail-closed, wenn keines da ist.
    listing([row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1, speaker_id: null })]);
    renderWithProviders(<UsersPage />);
    expect(await screen.findByTitle('Erst ein Sprecherprofil verknüpfen')).toBeDisabled();
  });

  it('🛑 sperrt das Einschalten auch bei einem Profil OHNE Stimmproben', async () => {
    // Das Backend verweigert BEIDE Faelle mit 409 — die Oberflaeche sperrte nur
    // den ersten. Der zweite lief ins 409 und zeigte die englische
    // Server-Meldung in der deutschen Maske.
    listing(
      [row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1, speaker_id: 7 })],
      [{ id: 7, name: 'leer', embedding_count: 0 }],
    );
    renderWithProviders(<UsersPage />);
    expect(
      await screen.findByTitle('Das verknüpfte Profil hat noch keine Stimmproben'),
    ).toBeDisabled();
  });

  it('sperrt NICHT, wenn die Sprecherliste gar nicht geladen werden konnte', async () => {
    // 🛑 `fetchSpeakers` schluckt Fehler und gibt `[]` zurueck. Aus einer leeren
    // Liste „keine Einbettungen" zu folgern, wuerde bei totem /api/speakers
    // jeden Schalter sperren — die Abwesenheit eines Profils ist kein Beweis.
    listing([row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1, speaker_id: 7 })], []);
    renderWithProviders(<UsersPage />);
    expect(await screen.findByTitle('Stimme als zweiten Faktor einschalten')).toBeEnabled();
  });

  it('am eigenen Konto mit scharfem Faktor wird das ABSCHALTEN angeboten', async () => {
    // Der Quadrant eigen+an: sonst haette sich der Eigentuemer selbst scharf
    // gestellt und keinen Weg zurueck gesehen.
    listing([row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1,
                   voice_second_factor_enabled: true })]);
    const { calls } = captureToggle();
    renderWithProviders(<UsersPage />);
    await userEvent.click(await screen.findByTitle('Stimme als zweiten Faktor abschalten'));
    await waitFor(() => expect(calls).toEqual([{ id: String(SELF), enabled: false }]));
  });

  it('🛑 unterscheidet „scharf" von „ruht"', async () => {
    // Fehlt eine Vorbedingung, haelt die Anmeldung die Token NICHT zurueck: die
    // Einwilligung steht, wirkt aber nicht. Ein Abzeichen, das beides gleich
    // zeigt, waere eine Anzeige, die luegt — und ein Administrator haette
    // geglaubt, das Konto sei geschuetzt.
    listing(
      [row({ id: 2, username: 'anna', speaker_id: null, voice_second_factor_enabled: true })],
      [],
    );
    renderWithProviders(<UsersPage />);
    expect(await screen.findByText('2. Faktor ruht (kein Stimmprofil)')).toBeInTheDocument();
    expect(screen.queryByText('Stimme als 2. Faktor')).not.toBeInTheDocument();
  });

  it('🛑 meldet „ruht", wenn der SPRACHWEG der Instanz aus ist', async () => {
    // Der Befund aus der Browser-Abnahme vom 2026-09-28: eine eingewilligte
    // Zeile zeigte „scharf", waehrend VOICE_AUTH_ENABLED=false die Huerde ruhen
    // liess. Die Maske kannte nur die Profil-Haelfte der Vorbedingungen.
    listing(
      [row({ id: 2, username: 'anna', voice_second_factor_enabled: true })],
      [{ id: 7, name: 'stimme', embedding_count: 3 }],
      { voice_auth_enabled: false },
    );
    renderWithProviders(<UsersPage />);
    expect(await screen.findByText('2. Faktor ruht (Sprachweg aus)')).toBeInTheDocument();
    expect(screen.queryByText('Stimme als 2. Faktor')).not.toBeInTheDocument();
  });

  it('meldet „ruht", wenn die SPRECHERKENNUNG aus ist', async () => {
    listing(
      [row({ id: 2, username: 'anna', voice_second_factor_enabled: true })],
      [{ id: 7, name: 'stimme', embedding_count: 3 }],
      { speaker_recognition_enabled: false },
    );
    renderWithProviders(<UsersPage />);
    expect(await screen.findByText('2. Faktor ruht (Sprachweg aus)')).toBeInTheDocument();
  });

  it('🛑 sperrt das EINSCHALTEN NICHT, nur weil der Sprachweg aus ist', async () => {
    // Die Gegenkontrolle gegen die Regression, die ich mir beim Fix fast gebaut
    // haette. Das Backend schliesst `voice_auth_enabled` bewusst aus dem 409 aus:
    // die Cutover-Reihenfolge lautet erst Einwilligungen einsammeln, DANN das
    // Flag umlegen. Sperrte die Maske hier, waere genau das unmoeglich.
    listing(
      [row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1 })],
      [{ id: 7, name: 'stimme', embedding_count: 3 }],
      { voice_auth_enabled: false },
    );
    const { calls } = captureToggle();

    renderWithProviders(<UsersPage />);
    const button = await screen.findByTitle('Stimme als zweiten Faktor einschalten');
    expect(button).toBeEnabled();
    await userEvent.click(button);
    await waitFor(() => expect(calls).toEqual([{ id: String(SELF), enabled: true }]));
  });

  it('sperrt das Einschalten SEHR WOHL, wenn die Sprecherkennung aus ist', async () => {
    // Die andere Haelfte: DIESEN Fall verweigert das Backend mit 409, also darf
    // die Maske ihn nicht ins 409 laufen lassen.
    listing(
      [row({ id: SELF, username: 'admin', role_name: 'Admin', role_id: 1 })],
      [{ id: 7, name: 'stimme', embedding_count: 3 }],
      { speaker_recognition_enabled: false },
    );
    renderWithProviders(<UsersPage />);
    expect(
      await screen.findByTitle('Sprecherkennung ist auf dieser Instanz aus'),
    ).toBeDisabled();
  });
});
