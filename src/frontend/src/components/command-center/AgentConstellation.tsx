// Command Center centerpiece — a live, structural constellation of the running
// system (docs/design/command-center.md). Deliberately NOT the "glowing orb"
// reference: DESIGN.md forbids decorative gradients/blobs, so this is a warm,
// legible board — solid crimson core, thin connectors, motion only where it
// carries meaning (the active turn, occupied rooms), reduced-motion honoured.
//
// Every node is a real entity and a drill-down link: roles → /admin/routing,
// tools → /admin/integrations, rooms → /admin/satellites, peers → /brain/audit.
// Hovering or focusing a role draws its reach-edges (which MCP servers the role
// may use, from agent_roles.yaml); hovering a tool shows the inverse.
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import type { CommandCenterModel, NodeHealth, RingStatus } from './types';
import { TRAIL_WINDOW_MS } from './useCommandCenterModel';
import { demoModel } from './demoData';

const C = 450; // svg centre (viewBox 900×900)
const R_CORE = 76;
const R_ROLES = 160;
const R_TOOLS = 268;
const R_ROOMS = 368;
const R_PEERS = 428;

const TOKEN = {
  core: 'var(--color-primary-600)',
  coreRing: 'var(--color-primary-700)',
  active: 'var(--color-accent-500)', // turquoise
  cream: 'var(--color-cream)',
  healthy: 'var(--color-accent-500)',
  degraded: 'var(--color-primary-300)',
  down: 'var(--color-primary-700)',
  unknown: 'var(--color-gray-400)',
} as const;

function polar(r: number, deg: number): [number, number] {
  const a = ((deg - 90) * Math.PI) / 180;
  return [C + r * Math.cos(a), C + r * Math.sin(a)];
}

function healthColor(h: NodeHealth): string {
  return TOKEN[h];
}

function anchorFor(x: number): 'start' | 'middle' | 'end' {
  const dx = x - C;
  if (dx > 2) return 'start';
  if (dx < -2) return 'end';
  return 'middle';
}

type Hover =
  | { kind: 'role'; id: string }
  | { kind: 'tool'; id: string }
  | null;

interface Props {
  model?: CommandCenterModel;
  className?: string;
  /** Calm "backend unreachable / system busy" core treatment (never an alarm —
   *  a saturated shared GPU is routine for a household). */
  muted?: boolean;
  /** Disable drill-down navigation (demo/design-review rendering). */
  interactive?: boolean;
}

/** Evenly-spread placeholder dots while a ring loads / after it errored. */
function RingPlaceholder({ r, status }: { r: number; status: RingStatus }) {
  const dots = Array.from({ length: 8 }, (_, i) => polar(r, (360 / 8) * i));
  return (
    <g className={status === 'loading' ? 'cc-loading' : undefined} aria-hidden="true">
      {dots.map(([x, y], i) => (
        <circle
          key={i}
          cx={x}
          cy={y}
          r={5}
          fill="none"
          stroke={status === 'error' ? TOKEN.down : 'var(--color-gray-400)'}
          strokeOpacity={status === 'error' ? 0.35 : 0.3}
          strokeWidth={1.5}
          strokeDasharray="2 3"
        />
      ))}
    </g>
  );
}

/** Read-only live constellation. Feed it a CommandCenterModel (assembled by
 *  useCommandCenterModel from the admin endpoints + the activity pulse);
 *  defaults to the demo model for standalone/design-review rendering. */
export default function AgentConstellation({
  model = demoModel,
  className,
  muted = false,
  interactive = true,
}: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [hover, setHover] = useState<Hover>(null);

  const { core, roles, tools, rooms, peers = [], trail = [], ringStatus = {} } = model;
  const activeRole = roles.find((r) => r.id === core.activeRoleId);
  // A role can be active without appearing in the ring (renamed role, ring
  // still loading) — show its raw id rather than a false "idle".
  const activeLabel = activeRole?.label ?? core.activeRoleId;

  // even angular spread per ring (0° = top, clockwise); each ring is offset by
  // half a slot vs its neighbour so labels don't stack along one radius
  const at = (i: number, n: number, offset = 0) =>
    n > 0 ? (360 / n) * i + offset : 0;
  const toolOffset = tools.length > 0 ? 180 / tools.length : 0;
  // peers occupy a top arc (-55°..55°) so they read as an outer cluster, not a ring
  const peerAngle = (i: number, n: number) => (n > 1 ? -55 + (110 / (n - 1)) * i : 0);

  // Newest activation per role (skip the active one — it gets the full
  // treatment) with a linear decay for the halo opacity. This is the
  // "heartbeat": you can see which parts of the household were just busy.
  const now = Date.now();
  const trailByRole = useMemo(() => {
    const map = new Map<string, number>();
    for (const entry of trail) {
      if (entry.roleId === core.activeRoleId) continue;
      if (!map.has(entry.roleId)) map.set(entry.roleId, entry.at);
    }
    return map;
  }, [trail, core.activeRoleId]);

  // role-id → set of tool ids it may reach (null reach = all tools)
  const reachOf = (roleId: string): Set<string> => {
    const role = roles.find((r) => r.id === roleId);
    if (!role) return new Set();
    if (role.reachServers == null) return new Set(tools.map((tool) => tool.id));
    return new Set(role.reachServers.filter((s) => tools.some((tool) => tool.id === s)));
  };
  const reachEdges: Array<{ roleId: string; toolId: string; broad: boolean }> = [];
  if (hover?.kind === 'role') {
    const role = roles.find((r) => r.id === hover.id);
    const broad = role?.reachServers == null;
    for (const toolId of reachOf(hover.id)) {
      reachEdges.push({ roleId: hover.id, toolId, broad });
    }
  } else if (hover?.kind === 'tool') {
    for (const role of roles) {
      if (reachOf(role.id).has(hover.id)) {
        reachEdges.push({ roleId: role.id, toolId: hover.id, broad: role.reachServers == null });
      }
    }
  }

  const angleOfRole = (id: string) => {
    const i = roles.findIndex((r) => r.id === id);
    return at(i, roles.length);
  };
  const angleOfTool = (id: string) => {
    const i = tools.findIndex((tool) => tool.id === id);
    return at(i, tools.length, toolOffset);
  };

  const go = (path: string) => {
    if (interactive) navigate(path);
  };
  const linkProps = (path: string, label: string) =>
    interactive
      ? {
          role: 'link' as const,
          tabIndex: 0,
          'aria-label': label,
          className: 'cc-node',
          onClick: () => go(path),
          onKeyDown: (event: React.KeyboardEvent) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              go(path);
            }
          },
        }
      : {};

  return (
    <div className={className}>
      <svg
        viewBox="0 0 900 900"
        className="w-full h-auto max-w-[820px] max-h-[76vh] mx-auto block"
        role="group"
        aria-labelledby="cc-title cc-desc"
      >
        <title id="cc-title">{t('commandCenter.title', { defaultValue: 'Command Center' })}</title>
        <desc id="cc-desc">
          {t('commandCenter.srSummary', {
            defaultValue:
              '{{roles}} agent roles, {{tools}} tools, {{rooms}} rooms. Active role: {{active}}.',
            roles: roles.length,
            tools: tools.length,
            rooms: rooms.length,
            active: activeLabel ?? t('commandCenter.idle', { defaultValue: 'idle' }),
          })}
        </desc>

        <style>{`
          .cc-core { transform-box: fill-box; transform-origin: center; animation: ccBreathe 5.5s ease-in-out infinite; }
          .cc-active-edge { stroke-dasharray: 6 8; animation: ccDash 1.1s linear infinite; }
          .cc-occupied { animation: ccPulse 2.8s ease-in-out infinite; }
          .cc-loading { animation: ccPulse 1.8s ease-in-out infinite; }
          .cc-node { cursor: pointer; outline: none; }
          .cc-focus { opacity: 0; }
          .cc-node:hover .cc-focus, .cc-node:focus-visible .cc-focus { opacity: 1; }
          @keyframes ccBreathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.025); } }
          @keyframes ccDash { to { stroke-dashoffset: -28; } }
          @keyframes ccPulse { 0%,100% { opacity: .55; } 50% { opacity: 1; } }
          @media (prefers-reduced-motion: reduce) {
            .cc-core, .cc-active-edge, .cc-occupied, .cc-loading { animation: none; }
          }
        `}</style>

        {/* faint ring guides — structure, not decoration */}
        {[R_ROLES, R_TOOLS, R_ROOMS].map((r) => (
          <circle
            key={r}
            cx={C}
            cy={C}
            r={r}
            fill="none"
            stroke="var(--color-gray-300)"
            strokeOpacity={0.25}
            strokeWidth={1}
          />
        ))}

        {/* core → role connectors (only the active one animates) */}
        {roles.map((role, i) => {
          const deg = at(i, roles.length);
          const [x, y] = polar(R_ROLES - 12, deg);
          const [cx, cy] = polar(R_CORE + 2, deg);
          const isActive = role.id === core.activeRoleId;
          return (
            <line
              key={`edge-${role.id}`}
              x1={cx}
              y1={cy}
              x2={x}
              y2={y}
              stroke={isActive ? TOKEN.active : 'var(--color-gray-400)'}
              strokeOpacity={isActive ? 0.9 : 0.22}
              strokeWidth={isActive ? 2.5 : 1.5}
              className={isActive && !muted ? 'cc-active-edge' : undefined}
            />
          );
        })}

        {/* hover/focus reach-edges: which tools a role may use (and inverse) */}
        {reachEdges.map(({ roleId, toolId, broad }) => {
          const [x1, y1] = polar(R_ROLES + 12, angleOfRole(roleId));
          const [x2, y2] = polar(R_TOOLS - 12, angleOfTool(toolId));
          return (
            <line
              key={`reach-${roleId}-${toolId}`}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke={TOKEN.active}
              strokeOpacity={broad ? 0.18 : 0.45}
              strokeWidth={1.5}
              pointerEvents="none"
            />
          );
        })}

        {/* ROOMS / SATELLITES ring (outermost full ring) */}
        {ringStatus.rooms && ringStatus.rooms !== 'ready' && rooms.length === 0 ? (
          <RingPlaceholder r={R_ROOMS} status={ringStatus.rooms} />
        ) : null}
        {rooms.map((room, i) => {
          const deg = at(i, rooms.length, toolOffset / 2);
          const [x, y] = polar(R_ROOMS, deg);
          const [lx, ly] = polar(R_ROOMS + 22, deg);
          const occupied = room.online && room.occupants > 0;
          const color = !room.online ? TOKEN.down : occupied ? TOKEN.active : TOKEN.unknown;
          return (
            <g
              key={`room-${room.id}`}
              {...linkProps(
                '/admin/satellites',
                t('commandCenter.openRoom', {
                  defaultValue: 'Open satellites: {{room}}',
                  room: room.label,
                }),
              )}
              onMouseEnter={() => setHover(null)}
            >
              <title>{room.hint ?? room.label}</title>
              <circle cx={x} cy={y} r={22} fill="transparent" stroke="none" />
              <circle className="cc-focus" cx={x} cy={y} r={15} fill="none" stroke={TOKEN.active} strokeWidth={2} strokeOpacity={0.7} />
              <circle
                cx={x}
                cy={y}
                r={10}
                fill={room.online ? color : 'none'}
                stroke={color}
                strokeWidth={2}
                strokeDasharray={room.online ? undefined : '3 3'}
                className={occupied && !muted ? 'cc-occupied' : undefined}
              />
              {occupied && (
                <text x={x} y={y + 3.5} textAnchor="middle" fontSize={10} fill="var(--color-gray-900)" fontWeight={600}>
                  {room.occupants}
                </text>
              )}
              <text
                x={lx}
                y={ly + 3}
                textAnchor={anchorFor(lx)}
                fontSize={13}
                fill="currentColor"
                className="text-gray-600 dark:text-gray-300"
              >
                {room.label}
              </text>
            </g>
          );
        })}

        {/* TOOLS / MCP ring */}
        {ringStatus.tools && ringStatus.tools !== 'ready' && tools.length === 0 ? (
          <RingPlaceholder r={R_TOOLS} status={ringStatus.tools} />
        ) : null}
        {tools.map((tool, i) => {
          const deg = at(i, tools.length, toolOffset);
          const [x, y] = polar(R_TOOLS, deg);
          const [lx, ly] = polar(R_TOOLS + 20, deg);
          const color = healthColor(tool.health);
          const highlighted =
            (hover?.kind === 'tool' && hover.id === tool.id) ||
            (hover?.kind === 'role' && reachEdges.some((e) => e.toolId === tool.id));
          return (
            <g
              key={`tool-${tool.id}`}
              {...linkProps(
                '/admin/integrations',
                t('commandCenter.openTool', {
                  defaultValue: 'Open integrations: {{tool}}',
                  tool: tool.label,
                }),
              )}
              onMouseEnter={() => setHover({ kind: 'tool', id: tool.id })}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover({ kind: 'tool', id: tool.id })}
              onBlur={() => setHover(null)}
            >
              <title>{tool.hint ?? tool.label}</title>
              <circle cx={x} cy={y} r={22} fill="transparent" stroke="none" />
              <circle className="cc-focus" cx={x} cy={y} r={15} fill="none" stroke={TOKEN.active} strokeWidth={2} strokeOpacity={0.7} />
              <rect
                x={x - 7}
                y={y - 7}
                width={14}
                height={14}
                rx={3}
                transform={`rotate(45 ${x} ${y})`}
                fill={tool.health === 'unknown' || tool.health === 'down' ? 'none' : color}
                stroke={color}
                strokeWidth={2}
                strokeDasharray={tool.health === 'down' ? '3 3' : undefined}
              />
              {tool.health === 'degraded' && (
                <text x={x} y={y + 4} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--color-primary-700)">
                  !
                </text>
              )}
              <text
                x={lx}
                y={ly + 3}
                textAnchor={anchorFor(lx)}
                fontSize={12}
                fill="currentColor"
                fontWeight={highlighted ? 600 : 400}
                className={highlighted ? 'text-gray-900 dark:text-white' : 'text-gray-500 dark:text-gray-400'}
              >
                {tool.label}
              </text>
            </g>
          );
        })}

        {/* ROLES ring */}
        {ringStatus.roles && ringStatus.roles !== 'ready' && roles.length === 0 ? (
          <RingPlaceholder r={R_ROLES} status={ringStatus.roles} />
        ) : null}
        {roles.map((role, i) => {
          const deg = at(i, roles.length);
          const [x, y] = polar(R_ROLES, deg);
          const [lx, ly] = polar(R_ROLES + 26, deg);
          const isActive = role.id === core.activeRoleId;
          const lastAt = trailByRole.get(role.id);
          // 0..1 recency of the last activation inside the trail window
          const recency = lastAt ? Math.max(0, 1 - (now - lastAt) / TRAIL_WINDOW_MS) : 0;
          const highlighted =
            hover?.kind === 'role'
              ? hover.id === role.id
              : hover?.kind === 'tool' && reachEdges.some((e) => e.roleId === role.id);
          return (
            <g
              key={`role-${role.id}`}
              {...linkProps(
                '/admin/routing',
                t('commandCenter.openRole', {
                  defaultValue: 'Open routing: {{role}}',
                  role: role.label,
                }),
              )}
              onMouseEnter={() => setHover({ kind: 'role', id: role.id })}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover({ kind: 'role', id: role.id })}
              onBlur={() => setHover(null)}
            >
              <title>{role.hint ?? role.label}</title>
              <circle cx={x} cy={y} r={22} fill="transparent" stroke="none" />
              <circle className="cc-focus" cx={x} cy={y} r={17} fill="none" stroke={TOKEN.active} strokeWidth={2} strokeOpacity={0.7} />
              {isActive && <circle cx={x} cy={y} r={16} fill={TOKEN.active} opacity={0.18} />}
              {!isActive && recency > 0 && (
                <circle
                  cx={x}
                  cy={y}
                  r={14}
                  fill="none"
                  stroke={TOKEN.active}
                  strokeWidth={2}
                  strokeOpacity={0.08 + recency * 0.3}
                />
              )}
              <circle
                cx={x}
                cy={y}
                r={10}
                fill={isActive ? TOKEN.active : TOKEN.cream}
                stroke={isActive ? TOKEN.active : 'var(--color-gray-400)'}
                strokeWidth={2}
              />
              <text
                x={lx}
                y={ly + 4}
                textAnchor={anchorFor(lx)}
                fontSize={14}
                fontWeight={isActive || highlighted ? 600 : 400}
                fill="currentColor"
                className={
                  isActive || highlighted
                    ? 'text-gray-900 dark:text-white'
                    : 'text-gray-600 dark:text-gray-300'
                }
              >
                {role.label}
              </text>
            </g>
          );
        })}

        {/* PEERS — outer top arc, only when present */}
        {peers.map((peer, i) => {
          const deg = peerAngle(i, peers.length);
          const [x, y] = polar(R_PEERS, deg);
          return (
            <g
              key={`peer-${peer.id}`}
              {...linkProps(
                '/brain/audit',
                t('commandCenter.openPeer', {
                  defaultValue: 'Open federation audit: {{peer}}',
                  peer: peer.label,
                }),
              )}
            >
              <title>{peer.label}</title>
              <circle cx={x} cy={y} r={20} fill="transparent" stroke="none" />
              <circle className="cc-focus" cx={x} cy={y} r={12} fill="none" stroke={TOKEN.active} strokeWidth={2} strokeOpacity={0.7} />
              <circle
                cx={x}
                cy={y}
                r={7}
                fill="none"
                stroke={peer.online ? TOKEN.active : TOKEN.unknown}
                strokeWidth={2}
                strokeDasharray="2 3"
              />
              <text
                x={x}
                y={y + 22}
                textAnchor="middle"
                fontSize={11}
                fill="currentColor"
                className="text-gray-500 dark:text-gray-400"
              >
                {peer.label}
              </text>
            </g>
          );
        })}

        {/* CORE */}
        <g className={muted ? undefined : 'cc-core'}>
          <circle
            cx={C}
            cy={C}
            r={R_CORE}
            fill={muted ? 'var(--color-gray-400)' : TOKEN.core}
            stroke={muted ? 'var(--color-gray-500)' : TOKEN.coreRing}
            strokeWidth={2}
          />
          <text x={C} y={C - 6} textAnchor="middle" fontSize={28} fill={TOKEN.cream} className="font-display">
            {core.label}
          </text>
          <text
            x={C}
            y={C + 20}
            textAnchor="middle"
            fontSize={12}
            fill={muted ? 'var(--color-gray-200)' : TOKEN.cream}
            opacity={activeRole || muted ? 1 : 0.75}
          >
            {muted
              ? t('commandCenter.busy', { defaultValue: 'system busy' })
              : activeLabel ?? t('commandCenter.idle', { defaultValue: 'idle' })}
          </text>
        </g>
      </svg>

      {/* legend + sr-only enumeration (Tier-0 a11y: status not by colour alone) */}
      <div className="mt-3 flex flex-wrap items-center justify-center gap-x-5 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
        {(['healthy', 'degraded', 'down', 'unknown'] as NodeHealth[]).map((h) => (
          <span key={h} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm"
              style={
                h === 'down' || h === 'unknown'
                  ? { border: `1.5px ${h === 'down' ? 'dashed' : 'solid'} ${healthColor(h)}` }
                  : { background: healthColor(h) }
              }
            />
            {t(`commandCenter.legend.${h}`, { defaultValue: h })}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: TOKEN.active }} />
          {t('commandCenter.legend.active', { defaultValue: 'Active / occupied' })}
        </span>
      </div>
    </div>
  );
}
