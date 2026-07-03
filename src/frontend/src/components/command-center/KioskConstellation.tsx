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
import { Music2, Radio as RadioIcon, Film, ListMusic } from 'lucide-react';

import { iconForCode } from '../chat/artifacts/WeatherArtifact';
import type { NodeHealth } from './types';
import type { CoreState, KioskState } from './useKioskModel';
import type { KioskNowPlaying } from '../../api/resources/commandCenter';

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

// Deterministic star field (no Math.random — SSR/rebuild-stable). `d` is a
// per-star twinkle delay so the field shimmers out of phase, not in lockstep.
const STARS = Array.from({ length: 140 }, (_, i) => {
  const a = (i * 2654435761) % 2147483647;
  const b = (i * 40503 + 12345) % 2147483647;
  return {
    x: (a % VW),
    y: (b % VH),
    r: 0.5 + ((a >> 8) % 10) / 8,
    o: 0.06 + ((b >> 6) % 30) / 120,
    d: ((a >> 4) % 60) / 10, // 0–6s
  };
});

interface Props {
  kiosk: KioskState;
}

export default function KioskConstellation({ kiosk }: Props) {
  const { t } = useTranslation();
  const { model, core, activeRoom, activeRoleLabel, telemetry, weather, nowPlaying } = kiosk;
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
          {/* Drifting nebula clouds — the dark field is no longer flat black. */}
          <radialGradient id="k-neb1" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#0f6b8f" stopOpacity={0.22} />
            <stop offset="100%" stopColor="#0f6b8f" stopOpacity={0} />
          </radialGradient>
          <radialGradient id="k-neb2" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#5a2b7a" stopOpacity={0.20} />
            <stop offset="100%" stopColor="#5a2b7a" stopOpacity={0} />
          </radialGradient>
          <radialGradient id="k-neb3" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#134b6b" stopOpacity={0.16} />
            <stop offset="100%" stopColor="#134b6b" stopOpacity={0} />
          </radialGradient>
          {/* Slow radar sweep: a one-sided soft glow rotated around the core. */}
          <linearGradient id="k-sweep" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={C.active} stopOpacity={0} />
            <stop offset="86%" stopColor={C.active} stopOpacity={0} />
            <stop offset="100%" stopColor={C.active} stopOpacity={0.09} />
          </linearGradient>
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
          .k-sweep  { transform-box: fill-box; transform-origin: center; animation: kSpin 60s linear infinite; }
          .k-twinkle { animation: kTwinkle 5s ease-in-out infinite; }
          .k-neb-a { transform-box: fill-box; transform-origin: center; animation: kNebA 34s ease-in-out infinite alternate; }
          .k-neb-b { transform-box: fill-box; transform-origin: center; animation: kNebB 46s ease-in-out infinite alternate; }
          .k-neb-c { transform-box: fill-box; transform-origin: center; animation: kNebC 40s ease-in-out infinite alternate; }
          @keyframes kBreathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.03); } }
          @keyframes kBloom { 0%,100% { opacity: .55; transform: scale(1); } 50% { opacity: .85; transform: scale(1.08); } }
          @keyframes kPulse { 0%,100% { opacity: .5; } 50% { opacity: 1; } }
          @keyframes kDash { to { stroke-dashoffset: -34; } }
          @keyframes kSpin { to { transform: rotate(360deg); } }
          @keyframes kTwinkle { 0%,100% { opacity: var(--o,0.3); } 50% { opacity: calc(var(--o,0.3) * 0.28); } }
          @keyframes kNebA { from { transform: translate(0,0) scale(1); } to { transform: translate(70px,44px) scale(1.12); } }
          @keyframes kNebB { from { transform: translate(0,0) scale(1.05); } to { transform: translate(-64px,-38px) scale(1); } }
          @keyframes kNebC { from { transform: translate(0,0) scale(1); } to { transform: translate(40px,-52px) scale(1.1); } }
          @media (prefers-reduced-motion: reduce) {
            .k-breathe, .k-bloom, .k-occ, .k-active-edge, .k-spike, .k-spike2,
            .k-sweep, .k-twinkle, .k-neb-a, .k-neb-b, .k-neb-c { animation: none; }
          }
        `}</style>

        <rect x={0} y={0} width={VW} height={VH} fill="url(#k-bg)" />

        {/* drifting nebula clouds (behind stars) — kills the flat-black look */}
        <ellipse cx={540} cy={360} rx={620} ry={460} fill="url(#k-neb1)" className={reduced ? undefined : 'k-neb-a'} />
        <ellipse cx={1430} cy={760} rx={680} ry={520} fill="url(#k-neb2)" className={reduced ? undefined : 'k-neb-b'} />
        <ellipse cx={1360} cy={250} rx={520} ry={420} fill="url(#k-neb3)" className={reduced ? undefined : 'k-neb-c'} />

        {/* slow radar sweep around the core (symmetric disc → clean rotation) */}
        {!reduced && (
          <g transform={`translate(${CX} ${CY})`}>
            <g className="k-sweep">
              <circle cx={0} cy={0} r={R_PEERS + 60} fill="url(#k-sweep)" />
            </g>
          </g>
        )}

        {STARS.map((s, i) => (
          <circle
            key={i}
            cx={s.x}
            cy={s.y}
            r={s.r}
            fill="#cfe6ff"
            opacity={s.o}
            className={reduced ? undefined : 'k-twinkle'}
            style={reduced ? undefined : ({ '--o': s.o, animationDelay: `${s.d}s` } as React.CSSProperties)}
          />
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
          // Online satellites are turquoise — BRIGHT + filled when someone's
          // there, DIM ring when the room is empty. Only a genuinely offline
          // satellite is crimson/dashed. (An online-but-empty room must never
          // read as "unknown/grey" — that was the earlier bug.)
          const col = room.online ? C.active : C.down;
          return (
            <g key={`room-${room.id}`}>
              {occupied && <circle cx={x} cy={y} r={30} fill={col} opacity={0.16} filter="url(#k-soft)" className={reduced ? undefined : 'k-occ'} />}
              <circle cx={x} cy={y} r={occupied ? 13 : 9}
                fill={occupied ? col : 'none'}
                stroke={col} strokeWidth={2.5}
                strokeOpacity={room.online ? (occupied ? 1 : 0.5) : 1}
                strokeDasharray={room.online ? undefined : '4 4'}
                filter={occupied ? 'url(#k-glow)' : undefined} />
              {occupied && <text x={x} y={y + 5} textAnchor="middle" fontSize={15} fontWeight={700} fill="#05121a">{room.occupants}</text>}
              <text x={lx} y={ly + 6} textAnchor={anchorFor(lx)} fontSize={19} fontWeight={occupied ? 600 : 500}
                fill={occupied ? '#eaf2ff' : room.online ? '#8fa6b8' : C.dim} letterSpacing="0.02em">{room.label}</text>
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
              <circle cx={x} cy={y} r={9} fill="none" stroke={peer.online ? C.active : C.down} strokeWidth={2.5} strokeDasharray="2 4" filter={peer.online ? 'url(#k-glow)' : undefined} />
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

      {/* weather tile (under the wordmark) — hidden when no reading */}
      {weather && <WeatherTile weather={weather} />}

      {/* now-playing (bottom center) — media-follow sessions, one per room */}
      {nowPlaying.length > 0 && (
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2">
          {nowPlaying.slice(0, 3).map((s, i) => (
            <NowPlaying key={`${s.room}-${i}`} session={s} />
          ))}
        </div>
      )}

      {/* legend — one row per visual encoding actually on screen */}
      <div className="absolute bottom-8 right-10 flex flex-col gap-1.5 text-[13px]">
        {([
          { label: t('kiosk.legend.present', { defaultValue: 'Present / healthy' }), swatch: { background: C.healthy } },
          { label: t('kiosk.legend.empty', { defaultValue: 'Online · empty' }), swatch: { border: `1.5px solid ${C.active}`, opacity: 0.6 } },
          { label: t('commandCenter.legend.degraded', { defaultValue: 'Degraded' }), swatch: { background: C.degraded } },
          { label: t('kiosk.legend.offline', { defaultValue: 'Offline' }), swatch: { border: `1.5px dashed ${C.down}` } },
        ]).map((row) => (
          <span key={row.label} className="inline-flex items-center justify-end gap-2 text-white/45">
            {row.label}
            <span className="inline-block w-2.5 h-2.5 rounded-full" style={row.swatch} />
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

function WeatherTile({ weather }: { weather: NonNullable<KioskState['weather']> }) {
  const Icon = iconForCode(weather.code);
  const hasRange = weather.high != null && weather.low != null;
  return (
    <div className="absolute top-[7.5rem] left-10 flex items-center gap-4 text-white">
      <Icon className="w-11 h-11 shrink-0" strokeWidth={1.4} style={{ color: C.active }} aria-hidden="true" />
      <div className="leading-tight">
        <div className="flex items-baseline gap-2">
          <span className="text-4xl font-display tabular-nums">{Math.round(weather.temp)}{weather.unit}</span>
          {hasRange && (
            <span className="text-sm text-white/45 tabular-nums">
              {Math.round(weather.high as number)}° / {Math.round(weather.low as number)}°
            </span>
          )}
        </div>
        <div className="text-sm text-white/55 mt-0.5">
          {weather.condition}
          {weather.location ? <span className="text-white/35"> · {weather.location}</span> : null}
        </div>
      </div>
    </div>
  );
}

function nowPlayingIcon(kind: string) {
  if (kind === 'radio') return RadioIcon;
  if (kind === 'dlna_video') return Film;
  if (kind === 'dlna_album') return ListMusic;
  return Music2;
}

function NowPlaying({ session }: { session: KioskNowPlaying }) {
  const Icon = nowPlayingIcon(session.kind);
  const line = session.title || session.subtitle || '';
  return (
    <div className="inline-flex items-center gap-2.5 px-4 py-1.5 rounded-full
      bg-white/[0.04] border border-white/10 backdrop-blur-sm max-w-[42rem]">
      <Icon className="w-4 h-4 shrink-0" style={{ color: C.active }} aria-hidden="true" />
      <span className="text-[13px] tracking-wide font-medium" style={{ color: C.active }}>
        {session.room}
      </span>
      {line && (
        <>
          <span className="text-white/25">·</span>
          <span className="text-[13px] text-white/70 truncate">{line}</span>
        </>
      )}
    </div>
  );
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
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
  );
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setReduced(mq.matches);
    on();
    // Older WebKit (some wall-display browsers) only has the legacy add/remove
    // Listener API — fall back so the effect never throws and blanks the kiosk.
    if (mq.addEventListener) {
      mq.addEventListener('change', on);
      return () => mq.removeEventListener('change', on);
    }
    mq.addListener(on);
    return () => mq.removeListener(on);
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
