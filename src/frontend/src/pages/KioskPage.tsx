// /kiosk — the fullscreen wall-display command center. Renders OUTSIDE the app
// Layout (no sidebar, no header) so it is truly edge-to-edge, and hides the
// cursor after a few idle seconds like a proper kiosk. Content-free + dark.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { X } from 'lucide-react';

import KioskConstellation from '../components/kiosk/KioskConstellation';
import { useKioskModel } from '../components/kiosk/useKioskModel';

export default function KioskPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const kiosk = useKioskModel();
  const [cursorHidden, setCursorHidden] = useState(false);
  const idleTimer = useRef<number | undefined>(undefined);

  // The kiosk takes over the whole viewport with no app chrome, so give it an
  // in-shell way back (it is reachable from the admin sidebar now): Escape, plus
  // a close button that fades in with the cursor and away when it idles.
  const exitKiosk = useCallback(() => navigate('/'), [navigate]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') exitKiosk();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [exitKiosk]);

  // Hide the cursor after 4s of no movement (wall-display polish).
  useEffect(() => {
    const bump = () => {
      setCursorHidden(false);
      window.clearTimeout(idleTimer.current);
      idleTimer.current = window.setTimeout(() => setCursorHidden(true), 4000);
    };
    bump();
    window.addEventListener('mousemove', bump);
    window.addEventListener('touchstart', bump);
    return () => {
      window.clearTimeout(idleTimer.current);
      window.removeEventListener('mousemove', bump);
      window.removeEventListener('touchstart', bump);
    };
  }, []);

  return (
    <div className="fixed inset-0 bg-black" style={{ cursor: cursorHidden ? 'none' : 'auto' }}>
      {/* In-shell exit — fades away with the cursor so it never mars the idle
          wall display; Escape does the same for keyboard users. */}
      <button
        type="button"
        onClick={exitKiosk}
        aria-label={t('kiosk.exit', { defaultValue: 'Exit kiosk' })}
        title={t('kiosk.exit', { defaultValue: 'Exit kiosk' })}
        className={`absolute top-6 right-6 z-20 p-2 rounded-full text-white/40
          hover:text-white/90 hover:bg-white/10 transition-opacity duration-500
          ${cursorHidden ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
      >
        <X className="w-5 h-5" />
      </button>
      {kiosk.bootLoading ? (
        <div className="w-full h-full flex items-center justify-center text-white/50 text-lg tracking-wide">
          {t('kiosk.loading', { defaultValue: 'Waking the command center…' })}
        </div>
      ) : (
        <>
          <KioskConstellation kiosk={kiosk} />
          {/* Every source failed after a first load — the constellation is now
              stale. Flag it plainly so a passerby doesn't read a frozen board
              as live household state. The core already reads 'system busy'. */}
          {kiosk.backendUnreachable && (
            <div
              role="status"
              className="absolute top-8 left-1/2 -translate-x-1/2 flex items-center gap-2
                px-4 py-2 rounded-full bg-[#e63e54]/15 border border-[#e63e54]/40 text-[#f7a4ae]
                text-sm tracking-wide backdrop-blur-sm"
            >
              <span className="inline-block w-2 h-2 rounded-full bg-[#e63e54]" />
              {t('kiosk.disconnected', { defaultValue: 'Reconnecting to Renfield…' })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
