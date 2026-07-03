// /kiosk — the fullscreen wall-display command center. Renders OUTSIDE the app
// Layout (no sidebar, no header) so it is truly edge-to-edge, and hides the
// cursor after a few idle seconds like a proper kiosk. Content-free + dark.
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import KioskConstellation from '../components/command-center/KioskConstellation';
import { useKioskModel } from '../components/command-center/useKioskModel';

export default function KioskPage() {
  const { t } = useTranslation();
  const kiosk = useKioskModel();
  const [cursorHidden, setCursorHidden] = useState(false);
  const idleTimer = useRef<number | undefined>(undefined);

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
      {kiosk.bootLoading ? (
        <div className="w-full h-full flex items-center justify-center text-white/50 text-lg tracking-wide">
          {t('kiosk.loading', { defaultValue: 'Waking the command center…' })}
        </div>
      ) : (
        <KioskConstellation kiosk={kiosk} />
      )}
    </div>
  );
}
