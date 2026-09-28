#!/usr/bin/env bash
# Lint the backend with ruff — auf dem HOST, nicht im Container.
#
# WARUM NICHT IM CONTAINER
# ------------------------
# `make lint-backend` lief bis 2026-09-27 als
#   docker compose exec -T backend ruff check /app --config /app/pyproject.toml
# und war doppelt unmöglich:
#
#   1. `pyproject.toml` liegt im Projektwurzelverzeichnis, der Bau-Kontext des
#      Backend-Bildes ist aber `src/backend/` — die Datei ist im Bild NIE
#      vorhanden. Fehler wortwörtlich:
#      `error: invalid value '/app/pyproject.toml' for '--config <CONFIG_OPTION>'`
#   2. `ruff` steht in `requirements-test.txt`. Das Produktionsbild installiert
#      nur `requirements.txt` — im Container gibt es `ruff` überhaupt nicht
#      (`sh: 1: ruff: not found`, geprüft am 2026-09-27 auf .159).
#
# Der Container war also nie der richtige Ort. ruff ist ein einzelnes Rust-Binary
# ohne Projektabhängigkeiten; es gehört auf den Host, genau wie eslint im
# Frontend-Ziel.
#
# WARUM DIE VERSION FESTGENAGELT IST
# ----------------------------------
# `RUF100` („unused noqa") urteilt über die AKTIVE Regelmenge. Ein Wechsel der
# ruff-Version verschiebt diese Menge und damit die Befundzahl — ein Tor, dessen
# Urteil von der zufällig installierten Version abhängt, ist kein Tor. Beim
# Anheben: Version hier UND in `src/backend/requirements-test.txt` ändern, dann
# `make lint-backend` einmal laufen lassen und die neue Lage bereinigen.
set -euo pipefail

RUFF_PINNED_VERSION="0.16.9"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

find_ruff() {
    for candidate in ruff "$HOME/.local/bin/ruff"; do
        command -v "$candidate" >/dev/null 2>&1 && { echo "$candidate"; return 0; }
    done
    return 1
}

if ! RUFF="$(find_ruff)"; then
    cat >&2 <<EOF
ruff ist nicht installiert. Einmalig:

    pipx install ruff==${RUFF_PINNED_VERSION}

(oder 'uv tool install ruff==${RUFF_PINNED_VERSION}'). ruff ist ein einzelnes
Binary — es braucht keine Projekt-Umgebung und kein Python des Projekts.
EOF
    exit 127
fi

FOUND_VERSION="$("$RUFF" --version | awk '{print $2}')"
if [[ "$FOUND_VERSION" != "$RUFF_PINNED_VERSION" ]]; then
    # WARNUNG, kein Abbruch: eine abweichende Version soll niemanden am Linten
    # hindern. Aber sie muss SICHTBAR sein, sonst rätselt man über Befunde, die
    # nur aus dem Versionsunterschied stammen.
    echo "⚠ ruff ${FOUND_VERSION} statt der festgenagelten ${RUFF_PINNED_VERSION} —" >&2
    echo "  Befundzahlen können abweichen (RUF100 hängt an der Regelmenge)." >&2
fi

cd "$ROOT"
exec "$RUFF" check "$@" src/backend tests
