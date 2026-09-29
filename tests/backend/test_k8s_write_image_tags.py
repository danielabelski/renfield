"""``bin/k8s-write-image-tags.sh`` — schreibt nach dem Deploy die ausgerollten
Bildmarken in die Manifeste zurueck.

Das Skript fasst fremde Dateien in ZWEI Repos an (oeffentlich + privat), deshalb
steht hier, was es NICHT anfassen darf:

* ``:latest`` bleibt ``:latest``. Ein gleitender Zeiger ist kein festgeschriebener
  Stand — die oeffentlichen Manifeste tragen ihn absichtlich. Beim ersten echten
  Lauf schrieb das Skript sieben davon auf die Tagesmarke um; ein damit frisch
  aufgesetzter Cluster haette fuer immer das Bild dieses Tages gezogen.
* Der Registry-Name bleibt stehen, Platzhalter wie echte Adresse. Die echte
  Adresse gehoert nicht ins oeffentliche Repo, der Platzhalter nicht ins private.
* Fremde Bilder (``voice-server``, ``kokoro-tts``) gehen das Skript nichts an —
  sie haben ihre eigene Versionsreihe.

Der Test faehrt das Skript als Unterprozess: es ist Shell, und genau die
sed-Ausdruecke sind die Stelle, an der es schiefging.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.backend]

_SKRIPT = Path(__file__).resolve().parents[2] / "bin" / "k8s-write-image-tags.sh"

# Beide Registry-Schreibweisen in einer Datei: Platzhalter (oeffentliches Repo)
# und echte Adresse (privates Repo). Keine darf sich veraendern.
_MANIFEST = """\
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: backend
          image: your-registry.example/renfield/backend:latest
        - name: frontend
          image: your-registry.example/renfield/frontend:latest
        - name: backend-privat
          image: registry.example.invalid/renfield/backend:2026-09-01-alt
        - name: frontend-privat
          image: registry.example.invalid/renfield/frontend:v2.15.29
        - name: voice-server
          image: your-registry.example/renfield/voice-server:v0.1.7
"""


def _lauf(verzeichnis: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SKRIPT), "--dir", str(verzeichnis), *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    pfad = tmp_path / "backend.yaml"
    pfad.write_text(_MANIFEST)
    return pfad


def test_latest_bleibt_stehen(manifest: Path) -> None:
    """Der eigentliche Befund: `latest` ueberlebt den Lauf — fuer BEIDE Bilder."""
    ergebnis = _lauf(manifest.parent, "--backend-tag", "2026-09-29-neu", "--frontend-tag", "2026-09-29-neu")
    assert ergebnis.returncode == 0, ergebnis.stderr

    inhalt = manifest.read_text()
    assert "renfield/backend:latest" in inhalt
    assert "renfield/frontend:latest" in inhalt


def test_konkrete_marken_werden_gesetzt(manifest: Path) -> None:
    """Gegenprobe zum Riegel: ohne sie wuerde `test_latest_bleibt_stehen` auch
    dann gruen, wenn das Skript ueberhaupt nichts mehr ersetzt."""
    _lauf(manifest.parent, "--backend-tag", "2026-09-29-neu", "--frontend-tag", "v2.16.0")

    inhalt = manifest.read_text()
    assert "renfield/backend:2026-09-29-neu" in inhalt
    assert "renfield/frontend:v2.16.0" in inhalt
    assert "2026-09-01-alt" not in inhalt
    assert "v2.15.29" not in inhalt


def test_registry_name_bleibt_unberuehrt(manifest: Path) -> None:
    """Der Praefix vor `renfield/` wird nie angefasst — sonst wandert die echte
    Adresse ins oeffentliche Repo oder der Platzhalter ins private."""
    _lauf(manifest.parent, "--backend-tag", "2026-09-29-neu", "--frontend-tag", "v2.16.0")

    inhalt = manifest.read_text()
    assert inhalt.count("your-registry.example/renfield/") == 3
    assert inhalt.count("registry.example.invalid/renfield/") == 2


def test_fremde_bilder_bleiben_unberuehrt(manifest: Path) -> None:
    """`voice-server` hat seine eigene Versionsreihe (v0.1.x)."""
    _lauf(manifest.parent, "--backend-tag", "2026-09-29-neu", "--frontend-tag", "v2.16.0")

    assert "renfield/voice-server:v0.1.7" in manifest.read_text()


def test_latest_praefix_ist_eine_echte_marke(tmp_path: Path) -> None:
    """Die Grenze sitzt eng: geschuetzt ist die Marke `latest`, nicht alles, was
    mit `latest` anfaengt. `latest-rc` ist ein festgeschriebener Stand und wird
    ersetzt wie jede andere Marke."""
    pfad = tmp_path / "rc.yaml"
    pfad.write_text("          image: your-registry.example/renfield/backend:latest-rc\n")

    _lauf(tmp_path, "--backend-tag", "2026-09-29-neu")

    assert pfad.read_text().strip().endswith("renfield/backend:2026-09-29-neu")


def test_reines_latest_manifest_meldet_nichts_zu_tun(tmp_path: Path) -> None:
    """Ein Manifest, das nur `latest` traegt, ist nach dem Riegel unveraendert —
    das Skript darf es dann auch nicht als geschrieben melden."""
    pfad = tmp_path / "nur-latest.yaml"
    original = "          image: your-registry.example/renfield/backend:latest\n"
    pfad.write_text(original)

    ergebnis = _lauf(tmp_path, "--backend-tag", "2026-09-29-neu")

    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "nichts zu tun" in ergebnis.stdout
    assert pfad.read_text() == original


def test_dry_run_schreibt_nicht(manifest: Path) -> None:
    ergebnis = _lauf(manifest.parent, "--backend-tag", "2026-09-29-neu", "--dry-run")

    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "dry-run" in ergebnis.stdout
    assert manifest.read_text() == _MANIFEST
