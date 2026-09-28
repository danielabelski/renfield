#!/usr/bin/env python3
"""Notausgang: die Stimme als zweiten Anmeldefaktor von der Schale aus abschalten.

🛑 DAS IST NICHT DER NORMALE WEG
=================================
Der normale Weg ist **Verwaltung → Benutzer → Schild-Schaltfläche**, bzw.
``POST /api/users/{id}/voice-second-factor`` mit ``{"enabled": false}``. Wer dort
hinkommt, soll dort bleiben: die Route protokolliert, prüft Berechtigungen und
kennt die Asymmetrie zwischen Ein- und Ausschalten.

Dieses Skript existiert für **genau einen** Fall, den die Route nicht bedienen
kann (Review-Befund F5):

    Die einzige Administratorin hat den Faktor für sich eingeschaltet und kann
    nicht sprechen — defektes Mikrofon, Erkältung, kaputter Browser.

Dann greift die Kette lückenlos gegen sie: ``/auth/login`` hält die Token
zurück, ``/auth/voice`` scheitert an der Stimme, und Abschalten verlangt
``users.manage`` — das auf einer Standardinstallation NUR die Admin-Rolle hat
(``models/permissions.py``, ``DEFAULT_ROLES``). Es gibt keinen zweiten
Administrator, der helfen könnte. **Kein unterstützter API-Aufruf stellt die
Instanz wieder her.** Schalenzugang hat der Betreiber aber, und das ist der
Notausgang, den solche Systeme an dieser Stelle haben.

Die Alternative wäre gewesen, das Einschalten für den letzten Administrator zu
verweigern — dann könnte ausgerechnet die Person mit dem größten Schutzbedarf
den Faktor als Einzige nie benutzen.

🛑 Was dieses Skript NICHT kann: einschalten. Das ist eine Einwilligung in
biometrische Verarbeitung (Art. 9 DSGVO), und die gibt eine Person über die
Oberfläche für sich selbst ab, nicht ein Betreiber über die Kommandozeile.

Aufruf (im Backend-Pod, oder mit gesetztem ``RENFIELD_BACKEND_DIR``):

    python bin/voice_2fa_emergency.py --list
    python bin/voice_2fa_emergency.py --username admin --off --dry-run
    python bin/voice_2fa_emergency.py --username admin --off

    kubectl -n renfield exec deploy/backend -- \\
        python /app/../bin/voice_2fa_emergency.py --username admin --off
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from pathlib import Path

# --- backend import path (identical block in every bin/backfill_*.py) ---------------
# These scripts are copied into the backend pod on their own, so this cannot live in a
# shared module: it is what makes the shared modules importable in the first place.


def _find_backend_dir(
    script: Path, env: Mapping[str, str] = os.environ, image_root: Path = Path("/app")
) -> Path:
    """The Renfield backend root: ``$RENFIELD_BACKEND_DIR`` (exclusive when set), else
    the repo layout ``bin/../src/backend``, else the image layout ``/app``."""
    override = env.get("RENFIELD_BACKEND_DIR")
    candidates = (
        [Path(override)] if override
        else [script.resolve().parent.parent / "src" / "backend", image_root]
    )
    for candidate in candidates:
        if (candidate / "services" / "__init__.py").is_file() and (candidate / "utils" / "config.py").is_file():
            return candidate
    print(
        f"{script.name}: Renfield backend not found (tried: {', '.join(map(str, candidates))}). "
        "Set RENFIELD_BACKEND_DIR to the directory holding services/ and utils/ "
        "(repo: src/backend, image: /app).",
        file=sys.stderr,
    )
    raise SystemExit(2)


_BACKEND = _find_backend_dir(Path(__file__))
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
# --- end backend import path ---------------------------------------------------------

from sqlalchemy import select  # noqa: E402

from models.database import User  # noqa: E402
from services.database import AsyncSessionLocal  # noqa: E402


async def list_armed() -> int:
    """Wer hat den Faktor scharf? Nur Benutzername und Id — keine weiteren Daten."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(User.id, User.username, User.speaker_id)
            .where(User.voice_second_factor_enabled.is_(True))
            .order_by(User.username)
        )).all()
    if not rows:
        print("Kein Konto hat die Stimme als zweiten Faktor eingeschaltet.")
        return 0
    print(f"{len(rows)} Konto/Konten mit scharfem zweiten Faktor:")
    for uid, username, speaker_id in rows:
        profil = f"Profil {speaker_id}" if speaker_id else "OHNE Profil (Huerde ruht)"
        print(f"  id={uid:<5} {username:<24} {profil}")
    return 0


async def switch_off(username: str, dry_run: bool) -> int:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(User).where(User.username == username)
        )).scalar_one_or_none()

        if user is None:
            print(f"Kein Konto mit dem Benutzernamen '{username}'.", file=sys.stderr)
            return 1
        if not user.voice_second_factor_enabled:
            print(f"'{username}' (id={user.id}): der Faktor ist bereits aus — nichts zu tun.")
            return 0

        if dry_run:
            print(f"[dry-run] '{username}' (id={user.id}): "
                  f"voice_second_factor_enabled True -> False")
            return 0

        user.voice_second_factor_enabled = False
        await db.commit()

    print(f"'{username}' (id={user.id}): voice_second_factor_enabled True -> False")
    print("WARNUNG: das war der Notausgang. Der normale Weg ist")
    print("  Verwaltung -> Benutzer -> Schild-Schaltflaeche.")
    print("Die Einwilligung ist damit zurueckgenommen; die Person kann sie dort")
    print("jederzeit selbst wieder erteilen.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Notausgang: zweiten Anmeldefaktor (Stimme) abschalten.",
    )
    ap.add_argument("--username", help="Konto, dessen Faktor abgeschaltet wird")
    ap.add_argument("--off", action="store_true",
                    help="den Faktor abschalten (die einzige Richtung, die dieses Skript kann)")
    ap.add_argument("--list", action="store_true",
                    help="zeigen, welche Konten den Faktor scharf haben")
    ap.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts schreiben")
    args = ap.parse_args()

    if args.list:
        return asyncio.run(list_armed())
    if not (args.username and args.off):
        ap.error("entweder --list, oder --username NAME --off")
    return asyncio.run(switch_off(args.username, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
