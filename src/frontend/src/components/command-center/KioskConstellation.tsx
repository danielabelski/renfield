// KioskConstellation — the FULLSCREEN, cinematic "stunning" command-center for
// a wall display. Deliberately breaks DESIGN.md (glow, bloom, radial gradients)
// per TODOS.md line 315 — the marketing/kiosk aesthetic that must stay OUT of
// the restrained admin `AgentConstellation`. This is a separate component so
// that boundary holds.
//
// It renders full-bleed on a dark cosmic field: a glowing core that reacts to
// real household voice activity (idle / listening / processing / speaking, from
// the satellites' own state), concentric rings of roles / tools / rooms / peers,
// and an ambient telemetry corner. Content-free by design (counts, role names,
// room names only) so it is safe on a shared screen.
import { useEffect, useMemo, useState } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import type { NodeHealth } from './types';
import type { CoreState, KioskState } from './useKioskModel';

const VW = 1920;
const VH = 1080;
const CX = VW / 2;
const CY = VH / 2;
const R_ROLES = 210;
const R_TOOLS = 340;
const R_ROOMS = 452;
const R_PEERS = 520;
const R_CORE = 96;

const C = {
  active: '#00e4b8', // turquoise
  healthy: '#00e4b8',
  degraded: '#f7a4ae',
  down: '#e63e54',
  unknown: '#5b6472',
  crimson: '#e63e54',
  cream: '#f0e6d3',
  dim: '#7d8794',
} as const;

const CORE_COLOR: Record<CoreState, string> = {
  idle: C.crimson,
  listening: C.active,
  processing: C.cream,
  speaking: C.active,
  busy: C.unknown,
};

function polar(r: number, deg: number): [number, number] {
  const a = ((deg - 90) * Math.PI) / 180;
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
}
/** Like polar but around the origin (0,0) — for groups translated to the core. */
function ray(r: number, deg: number): [number, number] {
  const a = ((deg - 90) * Math.PI) / 180;
  return [r * Math.cos(a), r * Math.sin(a)];
}
function healthColor(h: NodeHealth): string {
  return h === 'healthy' ? C.healthy : h === 'degraded' ? C.degraded : h === 'down' ? C.down : C.unknown;
}
function anchorFor(x: number): 'start' | 'middle' | 'end' {
  const dx = x - CX;
  if (dx > 4) return 'start';
  if (dx < -4) return 'end';
  return 'middle';
}

// Deterministic star field (no Math.random — SSR/rebuild-stable).
const STARS = Array.from({ length: 140 }, (_, i) => {
  const a = (i * 2654435761) % 2147483647;
  const b = (i * 40503 + 12345) % 2147483647;
  return { x: (a % VW), y: (b % VH), r: 0.5 + ((a >> 8) % 10) / 8, o: 0.06 + ((b >> 6) % 30) / 120 };
});

interface Props {
  kiosk: KioskState;
}

export default function KioskConstellation({ kiosk }: Props) {
  const { t } = useTranslation();
  const { model, core, activeRoom, activeRoleLabel, telemetry } = kiosk;
  const { roles, tools, rooms, peers = [] } = model;

  const reduced = usePrefersReducedMotion();
  const clock = useClock();

  const at = (i: number, n: number, offset = 0) => (n > 0 ? (360 / n) * i + offset : 0);
  const toolOffset = tools.length > 0 ? 180 / tools.length : 0;
  const peerAngle = (i: number, n: number) => (n > 1 ? -50 + (100 / (n - 1)) * i : 0);
  const coreColor = CORE_COLOR[core];

  // Radiating "voice" spikes when listening/speaking.
  const voiceActive = core === 'listening' || core === 'speaking';
  const spikes = useMemo(
    () => Array.from({ length: 48 }, (_, i) => (360 / 48) * i),
    [],
  );

  return (
    <div className="relative w-full h-full overflow-hidden bg-black select-none">
      <svg viewBox={`0 0 ${VW} ${VH}`} className="w-full h-full" preserveAspectRatio="xMidYMid slice" role="img"
        aria-label={t('kiosk.srSummary', {
          defaultValue: 'Renfield command center. {{present}} people present, {{online}} of {{total}} satellites online.',
          present: telemetry.peoplePresent, online: telemetry.satellitesOnline, total: telemetry.satellitesTotal,
        })}>
        <defs>
          <radialGradient id="k-bg" cx="50%" cy="46%" r="75%">
            <stop offset="0%" stopColor="#0d1524" />
            <stop offset="55%" stopColor="#080d18" />
            <stop offset="100%" stopColor="#03050a" />
          </radialGradient>
          <radialGradient id="k-core" cx="42%" cy="38%" r="70%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity={0.95} />
            <stop offset="30%" stopColor={coreColor} stopOpacity={0.95} />
            <stop offset="100%" stopColor={coreColor} stopOpacity={0.65} />
          </radialGradient>
          <radialGradient id="k-bloom" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor={coreColor} stopOpacity={0.5} />
            <stop offset="100%" stopColor={coreColor} stopOpacity={0} />
          </radialGradient>
          <filter id="k-glow" x="-120%" y="-120%" width="340%" height="340%">
            <feGaussianBlur stdDeviation="6" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <filter id="k-soft" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="2.4" />
          </filter>
        </defs>

        <style>{`
          .k-breathe { transform-box: fill-box; transform-origin: center; animation: kBreathe 6s ease-in-out infinite; }
          .k-bloom  { transform-box: fill-box; transform-origin: center; animation: kBloom 4.5s ease-in-out infinite; }
          .k-occ    { animation: kPulse 3s ease-in-out infinite; }
          .k-active-edge { stroke-dasharray: 7 10; animation: kDash 1s linear infinite; }
          .k-spike  { transform-box: fill-box; transform-origin: center; animation: kSpin 22s linear infinite; }
          .k-spike2 { transform-box: fill-box; transform-origin: center; animation: kSpin 30s linear infinite reverse; }
          @keyframes kBreathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.03); } }
          @keyframes kBloom { 0%,100% { opacity: .55; transform: scale(1); } 50% { opacity: .85; transform: scale(1.08); } }
          @keyframes kPulse { 0%,100% { opacity: .5; } 50% { opacity: 1; } }
          @keyframes kDash { to { stroke-dashoffset: -34; } }
          @keyframes kSpin { to { transform: rotate(360deg); } }
          @media (prefers-reduced-motion: reduce) {
            .k-breathe, .k-bloom, .k-occ, .k-active-edge, .k-spike, .k-spike2 { animation: none; }
          }
        `}</style>

        <rect x={0} y={0} width={VW} height={VH} fill="url(#k-bg)" />
        {STARS.map((s, i) => (
          <circle key={i} cx={s.x} cy={s.y} r={s.r} fill="#cfe6ff" opacity={s.o} />
        ))}

        {/* faint ring guides */}
        {[R_ROLES, R_TOOLS, R_ROOMS].map((r) => (
          <circle key={r} cx={CX} cy={CY} r={r} fill="none" stroke="#2a3550" strokeOpacity={0.35} strokeWidth={1} />
        ))}

        {/* core → role connectors, active one animates */}
        {roles.map((role, i) => {
          const [x, y] = polar(R_ROLES - 14, at(i, roles.length));
          const [cx, cy] = polar(R_CORE + 4, at(i, roles.length));
          const isActive = role.id === model.core.activeRoleId;
          return (
            <line key={`e-${role.id}`} x1={cx} y1={cy} x2={x} y2={y}
              stroke={isActive ? C.active : '#2f3a55'} strokeOpacity={isActive ? 0.9 : 0.28}
              strokeWidth={isActive ? 3 : 1.5} className={isActive && !reduced ? 'k-active-edge' : undefined} />
          );
        })}

        {/* ROOMS ring */}
        {rooms.map((room, i) => {
          const deg = at(i, rooms.length, toolOffset / 2);
          const [x, y] = polar(R_ROOMS, deg);
          const [lx, ly] = polar(R_ROOMS + 30, deg);
          const occupied = room.online && room.occupants > 0;
          const col = !room.online ? C.down : occupied ? C.active : C.unknown;
          return (
            <g key={`room-${room.id}`}>
              {occupied && <circle cx={x} cy={y} r={30} fill={col} opacity={0.16} filter="url(#k-soft)" className={reduced ? undefined : 'k-occ'} />}
              <circle cx={x} cy={y} r={13} fill={room.online ? col : 'none'} stroke={col} strokeWidth={2.5}
                strokeDasharray={room.online ? undefined : '4 4'} filter={room.online ? 'url(#k-glow)' : undefined} />
              {occupied && <text x={x} y={y + 5} textAnchor="middle" fontSize={15} fontWeight={700} fill="#05121a">{room.occupants}</text>}
              <text x={lx} y={ly + 6} textAnchor={anchorFor(lx)} fontSize={19} fontWeight={600} fill={occupied ? '#eaf2ff' : C.dim} letterSpacing="0.02em">{room.label}</text>
            </g>
          );
        })}

        {/* TOOLS ring */}
        {tools.map((tool, i) => {
          const deg = at(i, tools.length, toolOffset);
          const [x, y] = polar(R_TOOLS, deg);
          const [lx, ly] = polar(R_TOOLS + 22, deg);
          const col = healthColor(tool.health);
          const on = tool.health === 'healthy' || tool.health === 'degraded';
          return (
            <g key={`tool-${tool.id}`}>
              <rect x={x - 8} y={y - 8} width={16} height={16} rx={3} transform={`rotate(45 ${x} ${y})`}
                fill={on ? col : 'none'} stroke={col} strokeWidth={2.5} strokeDasharray={tool.health === 'down' ? '3 3' : undefined}
                filter={on ? 'url(#k-glow)' : undefined} />
              <text x={lx} y={ly + 5} textAnchor={anchorFor(lx)} fontSize={16} fill={C.dim}>{tool.label}</text>
            </g>
          );
        })}

        {/* ROLES ring */}
        {roles.map((role, i) => {
          const deg = at(i, roles.length);
          const [x, y] = polar(R_ROLES, deg);
          const [lx, ly] = polar(R_ROLES - 30, deg);
          const isActive = role.id === model.core.activeRoleId;
          return (
            <g key={`role-${role.id}`}>
              {isActive && <circle cx={x} cy={y} r={22} fill={C.active} opacity={0.22} filter="url(#k-soft)" />}
              <circle cx={x} cy={y} r={12} fill={isActive ? C.active : '#0d1524'} stroke={isActive ? C.active : '#3a4763'}
                strokeWidth={2.5} filter={isActive ? 'url(#k-glow)' : undefined} />
              <text x={lx} y={ly + 6} textAnchor={anchorFor(lx)} fontSize={18} fontWeight={isActive ? 700 : 500}
                fill={isActive ? '#eafffb' : C.dim} letterSpacing="0.04em">{role.label.toUpperCase()}</text>
            </g>
          );
        })}

        {/* PEERS outer arc */}
        {peers.map((peer, i) => {
          const deg = peerAngle(i, peers.length);
          const [x, y] = polar(R_PEERS, deg);
          return (
            <g key={`peer-${peer.id}`}>
              <circle cx={x} cy={y} r={9} fill="none" stroke={peer.online ? C.active : C.unknown} strokeWidth={2.5} strokeDasharray="2 4" filter={peer.online ? 'url(#k-glow)' : undefined} />
              <text x={x} y={y - 18} textAnchor="middle" fontSize={15} fill={C.dim}>{peer.label}</text>
            </g>
          );
        })}

        {/* voice spikes when listening/speaking — a burst ring around the core.
            Coordinates are around the ORIGIN and the group is translated to the
            core, so the CSS rotation (transform-origin: center) spins around the
            core instead of the group's own displaced bbox. */}
        {voiceActive && !reduced && (
          <g opacity={0.85} transform={`translate(${CX} ${CY})`}>
            <g className="k-spike">
              {spikes.map((deg, i) => {
                const len = 22 + ((i * 37) % 40);
                const [x1, y1] = ray(R_CORE + 6, deg);
                const [x2, y2] = ray(R_CORE + 6 + len, deg);
                return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={coreColor} strokeWidth={2} strokeLinecap="round" />;
              })}
            </g>
            <g className="k-spike2" opacity={0.5}>
              {spikes.filter((_, i) => i % 2 === 0).map((deg, i) => {
                const len = 14 + ((i * 53) % 28);
                const [x1, y1] = ray(R_CORE + 10, deg);
                const [x2, y2] = ray(R_CORE + 10 + len, deg);
                return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={coreColor} strokeWidth={1.5} strokeLinecap="round" />;
              })}
            </g>
          </g>
        )}

        {/* CORE bloom + orb */}
        <circle cx={CX} cy={CY} r={R_CORE * 2.6} fill="url(#k-bloom)" className={reduced ? undefined : 'k-bloom'} />
        <g className={reduced ? undefined : 'k-breathe'}>
          <circle cx={CX} cy={CY} r={R_CORE} fill="url(#k-core)" filter="url(#k-glow)" />
          <text x={CX} y={CY - 8} textAnchor="middle" fontSize={46} fill="#fff" className="font-display" style={{ letterSpacing: '0.01em' }}>Renfield</text>
          <text x={CX} y={CY + 26} textAnchor="middle" fontSize={17} fontWeight={700} fill="#08131b" letterSpacing="0.22em">
            {coreCaption(core, t).toUpperCase()}
          </text>
        </g>
        {(activeRoom || activeRoleLabel) && (
          <text x={CX} y={CY + R_CORE + 40} textAnchor="middle" fontSize={17} fill={coreColor} letterSpacing="0.06em">
            {core === 'idle' && activeRoleLabel
              ? activeRoleLabel
              : activeRoom
                ? t('kiosk.inRoom', { defaultValue: '{{state}} · {{room}}', state: coreCaption(core, t), room: activeRoom })
                : ''}
          </text>
        )}
      </svg>

      {/* ---- HTML overlays (crisp typography over the SVG) ---- */}
      {/* wordmark */}
      <div className="absolute top-8 left-10 leading-tight">
        <div className="font-display text-4xl text-white tracking-wide">RENFIELD</div>
        <div className="text-[13px] tracking-[0.35em] mt-1" style={{ color: C.active }}>COMMAND CENTER</div>
      </div>

      {/* clock + daypart */}
      <div className="absolute bottom-8 left-10 text-white/90">
        <div className="text-5xl font-display tabular-nums">{clock.time}</div>
        <div className="text-sm text-white/50 mt-1 capitalize">{clock.date}</div>
      </div>

      {/* ambient telemetry corner */}
      <div className="absolute top-8 right-10 text-right space-y-3">
        <Telem label={t('kiosk.telemetry.satellites', { defaultValue: 'Satellites' })}
          value={`${telemetry.satellitesOnline}/${telemetry.satellitesTotal} ${t('kiosk.online', { defaultValue: 'online' })}`}
          alert={telemetry.satellitesOnline < telemetry.satellitesTotal} />
        <Telem label={t('kiosk.telemetry.present', { defaultValue: 'Present' })}
          value={t('kiosk.presentValue', { defaultValue: '{{people}} in {{rooms}} rooms', people: telemetry.peoplePresent, rooms: telemetry.occupiedRooms })} />
        <Telem label={t('kiosk.telemetry.tools', { defaultValue: 'Tools' })}
          value={`${telemetry.toolsHealthy}/${telemetry.toolsTotal} ${t('kiosk.healthy', { defaultValue: 'healthy' })}`}
          alert={telemetry.toolsHealthy < telemetry.toolsTotal} />
        <Telem label={t('kiosk.telemetry.agent', { defaultValue: 'Agent' })}
          value={activeRoleLabel ?? t('kiosk.idle', { defaultValue: 'idle' })} />
      </div>

      {/* legend */}
      <div className="absolute bottom-8 right-10 flex flex-col gap-1.5 text-[13px]">
        {([['healthy', t('commandCenter.legend.healthy', { defaultValue: 'Healthy / present' })],
           ['degraded', t('commandCenter.legend.degraded', { defaultValue: 'Degraded' })],
           ['down', t('commandCenter.legend.down', { defaultValue: 'Down / offline' })],
           ['unknown', t('commandCenter.legend.unknown', { defaultValue: 'Idle / unknown' })]] as [NodeHealth, string][]).map(([h, label]) => (
          <span key={h} className="inline-flex items-center justify-end gap-2 text-white/45">
            {label}
            <span className="inline-block w-2.5 h-2.5 rounded-full"
              style={h === 'down' || h === 'unknown'
                ? { border: `1.5px ${h === 'down' ? 'dashed' : 'solid'} ${healthColor(h)}` }
                : { background: healthColor(h) }} />
          </span>
        ))}
      </div>
    </div>
  );
}

function coreCaption(core: CoreState, t: TFunction): string {
  switch (core) {
    case 'listening': return t('kiosk.state.listening', { defaultValue: 'listening' });
    case 'processing': return t('kiosk.state.processing', { defaultValue: 'thinking' });
    case 'speaking': return t('kiosk.state.speaking', { defaultValue: 'speaking' });
    case 'busy': return t('kiosk.state.busy', { defaultValue: 'system busy' });
    default: return t('kiosk.state.idle', { defaultValue: 'ready' });
  }
}

function Telem({ label, value, alert }: { label: string; value: string; alert?: boolean }) {
  return (
    <div>
      <div className="text-[11px] tracking-[0.25em] text-white/35 uppercase">{label}</div>
      <div className={`text-lg font-medium tabular-nums ${alert ? 'text-[#f7a4ae]' : 'text-[#00e4b8]'}`}>{value}</div>
    </div>
  );
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setReduced(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return reduced;
}

function useClock(): { time: string; date: string } {
  const { i18n } = useTranslation();
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 10_000);
    return () => clearInterval(id);
  }, []);
  const locale = i18n.language?.startsWith('de') ? 'de-DE' : 'en-US';
  return {
    time: now.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' }),
    date: now.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long' }),
  };
}
