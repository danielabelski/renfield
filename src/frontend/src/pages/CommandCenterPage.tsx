// /admin/command-center — the unified "mission control" board
// (docs/design/command-center.md, Phase 1+2). Read-first: one glance answers
// "what is Renfield doing right now"; every node drills into the admin page
// that owns it. The constellation is desktop/kiosk-first — below lg the board
// yields to a grouped list with the same live statuses.
import { useMemo } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import {
  Activity,
  Bot,
  CheckCircle2,
  CloudOff,
  Maximize2,
  Radar,
  Server,
  Sofa,
  XCircle,
} from 'lucide-react';

import PageHeader from '../components/PageHeader';
import AgentConstellation from '../components/command-center/AgentConstellation';
import { useCommandCenterModel } from '../components/command-center/useCommandCenterModel';
import { roleLabel } from '../components/chat/AgentRoleBadge';
import type {
  CommandCenterModel,
  NodeHealth,
  PulseEntry,
} from '../components/command-center/types';

const HEALTH_DOT: Record<NodeHealth, string> = {
  healthy: 'bg-accent-500',
  degraded: 'bg-primary-300',
  down: 'border-2 border-dashed border-primary-700 bg-transparent',
  unknown: 'border-2 border-gray-400 bg-transparent',
};

function relativeTime(atMs: number, t: TFunction): string {
  const delta = Date.now() - atMs;
  if (delta < 60_000) return t('commandCenter.justNow', { defaultValue: 'just now' });
  const minutes = Math.round(delta / 60_000);
  if (minutes < 60) {
    return t('commandCenter.minutesAgo', { defaultValue: '{{n}} min ago', n: minutes });
  }
  return new Date(atMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** The content-free heartbeat rail: which roles just answered, when, and
 *  whether their tool run succeeded. No message text, no user names. */
function ActivityRail({ trail }: { trail: PulseEntry[] }) {
  const { t } = useTranslation();
  const entries = trail.slice(0, 12);
  return (
    <section className="card" aria-label={t('commandCenter.activityTitle', { defaultValue: 'Live activity' })}>
      <h2 className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-200">
        <Activity className="w-4 h-4 text-primary-600 dark:text-primary-400" aria-hidden="true" />
        {t('commandCenter.activityTitle', { defaultValue: 'Live activity' })}
      </h2>
      {entries.length === 0 ? (
        <p className="mt-3 text-sm text-gray-500 dark:text-gray-400">
          {t('commandCenter.activityEmpty', {
            defaultValue: 'Quiet right now — the board lights up with the next question.',
          })}
        </p>
      ) : (
        <ol className="mt-3 space-y-2" aria-live="polite">
          {entries.map((entry, i) => (
            <li
              key={`${entry.roleId}-${entry.at}`}
              className={`flex items-center gap-2 text-sm ${i === 0 ? 'text-gray-900 dark:text-white' : 'text-gray-500 dark:text-gray-400'}`}
            >
              <Bot className="w-3.5 h-3.5 shrink-0 opacity-70" aria-hidden="true" />
              <span className="font-medium">{roleLabel(t, entry.roleId)}</span>
              {entry.ok === true && (
                <CheckCircle2 className="w-3.5 h-3.5 shrink-0 text-accent-600" aria-label={t('commandCenter.turnOk', { defaultValue: 'succeeded' })} />
              )}
              {entry.ok === false && (
                <XCircle className="w-3.5 h-3.5 shrink-0 text-primary-700" aria-label={t('commandCenter.turnFailed', { defaultValue: 'failed' })} />
              )}
              <span className="ml-auto text-xs tabular-nums">{relativeTime(entry.at, t)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** Narrow-width fallback: the same entities and statuses as grouped link lists
 *  (the constellation is unreadable at phone widths — design doc open q. 1). */
function CommandCenterList({ model }: { model: CommandCenterModel }) {
  const { t } = useTranslation();
  const sections = [
    {
      key: 'roles',
      icon: Bot,
      title: t('commandCenter.ring.roles', { defaultValue: 'Agent roles' }),
      to: '/admin/routing',
      items: model.roles.map((role) => ({
        id: role.id,
        label: role.label,
        active: role.id === model.core.activeRoleId,
        health: undefined as NodeHealth | undefined,
        detail:
          role.id === model.core.activeRoleId
            ? t('commandCenter.activeNow', { defaultValue: 'answering now' })
            : undefined,
      })),
    },
    {
      key: 'tools',
      icon: Server,
      title: t('commandCenter.ring.tools', { defaultValue: 'Tools & integrations' }),
      to: '/admin/integrations',
      items: model.tools.map((tool) => ({
        id: tool.id,
        label: tool.label,
        active: false,
        health: tool.health,
        detail: t(`commandCenter.legend.${tool.health}`, { defaultValue: tool.health }),
      })),
    },
    {
      key: 'rooms',
      icon: Sofa,
      title: t('commandCenter.ring.rooms', { defaultValue: 'Rooms & satellites' }),
      to: '/admin/satellites',
      items: model.rooms.map((room) => ({
        id: room.id,
        label: room.label,
        active: room.online && room.occupants > 0,
        health: (room.online ? (room.occupants > 0 ? 'healthy' : 'unknown') : 'down') as NodeHealth,
        detail: !room.online
          ? t('commandCenter.legend.down', { defaultValue: 'down' })
          : room.occupants > 0
            ? t('commandCenter.occupants', { defaultValue: '{{count}} present', count: room.occupants })
            : t('commandCenter.emptyRoom', { defaultValue: 'empty' }),
      })),
    },
    ...(model.peers && model.peers.length > 0
      ? [
          {
            key: 'peers',
            icon: Radar,
            title: t('commandCenter.ring.peers', { defaultValue: 'Federation peers' }),
            to: '/brain/audit',
            items: model.peers.map((peer) => ({
              id: peer.id,
              label: peer.label,
              active: false,
              health: (peer.online ? 'healthy' : 'unknown') as NodeHealth,
              detail: peer.online
                ? t('commandCenter.reachable', { defaultValue: 'reachable' })
                : t('commandCenter.unreachable', { defaultValue: 'unreachable' }),
            })),
          },
        ]
      : []),
  ];

  return (
    <div className="space-y-4">
      {sections.map((section) => (
        <section key={section.key} className="card" aria-label={section.title}>
          <h2 className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-200">
            <section.icon className="w-4 h-4 text-primary-600 dark:text-primary-400" aria-hidden="true" />
            {section.title}
          </h2>
          {section.items.length === 0 ? (
            <p className="mt-3 text-sm text-gray-500 dark:text-gray-400">
              {t('commandCenter.sectionEmpty', { defaultValue: 'Nothing configured yet.' })}
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-gray-100 dark:divide-gray-700">
              {section.items.map((item) => (
                <li key={item.id}>
                  <Link
                    to={section.to}
                    className="flex min-h-11 items-center gap-3 py-2 text-sm text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 rounded-sm"
                  >
                    <span
                      className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${
                        item.health ? HEALTH_DOT[item.health] : item.active ? 'bg-accent-500' : 'bg-gray-300 dark:bg-gray-600'
                      }`}
                      aria-hidden="true"
                    />
                    <span className={item.active ? 'font-semibold' : undefined}>{item.label}</span>
                    {item.detail && (
                      <span className="ml-auto text-xs text-gray-400 dark:text-gray-500">{item.detail}</span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

export default function CommandCenterPage() {
  const { t } = useTranslation();
  const { model, bootLoading, backendUnreachable, trail } = useCommandCenterModel();

  const occupiedRooms = useMemo(
    () => model.rooms.filter((room) => room.online && room.occupants > 0).length,
    [model.rooms],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Radar}
        title={t('commandCenter.title', { defaultValue: 'Command Center' })}
        subtitle={t('commandCenter.subtitle', {
          defaultValue: 'Live constellation of the running system',
        })}
      >
        <Link
          to="/kiosk"
          target="_blank"
          rel="noopener"
          className="btn btn-secondary inline-flex items-center gap-2"
          title={t('commandCenter.openKiosk', { defaultValue: 'Open the fullscreen wall display' })}
        >
          <Maximize2 className="w-4 h-4" aria-hidden="true" />
          {t('commandCenter.kiosk', { defaultValue: 'Kiosk' })}
        </Link>
      </PageHeader>

      {backendUnreachable && (
        <div
          role="status"
          className="card flex items-center gap-3 border-primary-200 dark:border-primary-900/50"
        >
          <CloudOff className="w-5 h-5 shrink-0 text-primary-700 dark:text-primary-400" aria-hidden="true" />
          <p className="text-sm text-gray-700 dark:text-gray-300">
            {t('commandCenter.unreachableHint', {
              defaultValue:
                'Backend unreachable or busy — showing the last known state. The board recovers on its own.',
            })}
          </p>
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
        {/* the board (desktop/kiosk); list fallback below lg */}
        <div className="min-w-0">
          {bootLoading ? (
            <div className="card flex items-center justify-center min-h-96" aria-busy="true">
              <div className="text-center space-y-3">
                <Radar className="w-8 h-8 mx-auto animate-pulse text-primary-300" aria-hidden="true" />
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {t('commandCenter.loading', { defaultValue: 'Assembling the constellation…' })}
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="hidden lg:block card">
                <AgentConstellation model={model} muted={backendUnreachable} />
              </div>
              <div className="lg:hidden">
                <CommandCenterList model={model} />
              </div>
            </>
          )}
        </div>

        {/* right rail: live activity + a one-line situational summary */}
        <div className="space-y-4">
          <section className="card" aria-label={t('commandCenter.summaryTitle', { defaultValue: 'At a glance' })}>
            <h2 className="text-sm font-medium text-gray-700 dark:text-gray-200">
              {t('commandCenter.summaryTitle', { defaultValue: 'At a glance' })}
            </h2>
            <dl className="mt-3 space-y-1.5 text-sm text-gray-600 dark:text-gray-300">
              <div className="flex justify-between gap-2">
                <dt>{t('commandCenter.ring.tools', { defaultValue: 'Tools & integrations' })}</dt>
                <dd className="tabular-nums">
                  {model.tools.filter((tool) => tool.health === 'healthy').length}/{model.tools.length}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>{t('commandCenter.ring.rooms', { defaultValue: 'Rooms & satellites' })}</dt>
                <dd className="tabular-nums">
                  {model.rooms.filter((room) => room.online).length}/{model.rooms.length}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>{t('commandCenter.occupiedRooms', { defaultValue: 'Occupied rooms' })}</dt>
                <dd className="tabular-nums">{occupiedRooms}</dd>
              </div>
            </dl>
          </section>
          <ActivityRail trail={trail} />
        </div>
      </div>
    </div>
  );
}
