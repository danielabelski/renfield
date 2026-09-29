#!/usr/bin/env bash
# Schreibt die frisch ausgerollten Bildmarken in die Manifeste — committet NICHT.
#
# WARUM ES DAS GIBT
# -----------------
# `kubectl set image` aendert die LIVE-Objekte, nicht die Manifeste. Nach jedem
# Deploy driften die Dateien also erneut, und ein spaeteres `kubectl apply -f`
# wirft die Instanz auf das alte Bild zurueck. `bin/k8s-drift-check.sh` merkt das
# und WARNT — behoben wurde es danach von Hand, nach jedem Deploy aufs Neue.
# Dreimal in Folge hiess der naechste Commit in x-ren "Bildmarken auf den
# Live-Stand". Eine Handarbeit, an die man denken muss, ist keine Loesung.
#
# 🛑 NUR DIE MARKE, NIE DER REGISTRY-NAME. Die oeffentlichen Manifeste tragen
# absichtlich den Platzhalter `your-registry.example/...`; die echte Adresse
# gehoert nicht ins oeffentliche Repo. Die Ersetzung greift deshalb ausschliesslich
# hinter `renfield/<bild>:` und laesst alles davor unberuehrt — egal ob dort der
# Platzhalter oder die echte Adresse steht.
#
# 🛑 COMMITTET UND PUSHT NICHT. Das Skript sagt, was zu committen ist; die
# Entscheidung bleibt beim Menschen. Das gilt besonders, weil die Manifeste einer
# privaten Instanz in einem ZWEITEN Repo liegen.
#
# Aufruf:
#   bin/k8s-write-image-tags.sh --dir ../x-ren/k8s --backend-tag 2026-09-28-x \
#                               --frontend-tag 2026-09-28-y [--dry-run]
set -euo pipefail

DIR="" BACKEND_TAG="" FRONTEND_TAG="" DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)           DIR="$2"; shift 2 ;;
    --backend-tag)   BACKEND_TAG="$2"; shift 2 ;;
    --frontend-tag)  FRONTEND_TAG="$2"; shift 2 ;;
    --dry-run)       DRY_RUN=1; shift ;;
    -h|--help)       sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -z "$DIR" ]] && { echo "ERROR: --dir required" >&2; exit 2; }
[[ -d "$DIR" ]] || { echo "ERROR: no such directory: $DIR" >&2; exit 2; }
[[ -z "$BACKEND_TAG$FRONTEND_TAG" ]] && { echo "  (nichts zu schreiben: weder --backend-tag noch --frontend-tag)"; exit 0; }

# Eine Marke darf keine Zeichen tragen, die die Ersetzung oder YAML zerlegen.
for t in "$BACKEND_TAG" "$FRONTEND_TAG"; do
  [[ -z "$t" ]] && continue
  [[ "$t" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: unplausible tag: $t" >&2; exit 2; }
done

changed=() fehlgeschlagen=()
while IFS= read -r -d '' f; do
  before="$(cat "$f")"
  after="$before"
  # Nur der Teil HINTER `renfield/<bild>:` wird ersetzt. Der Praefix — und damit
  # der Registry-Name, echt oder Platzhalter — bleibt unangetastet.
  if [[ -n "$BACKEND_TAG" ]]; then
    after="$(printf '%s' "$after" | sed -E "s#(renfield/backend:)[A-Za-z0-9._-]+#\1${BACKEND_TAG}#g")"
  fi
  if [[ -n "$FRONTEND_TAG" ]]; then
    after="$(printf '%s' "$after" | sed -E "s#(renfield/frontend:)[A-Za-z0-9._-]+#\1${FRONTEND_TAG}#g")"
  fi
  [[ "$after" == "$before" ]] && continue

  if [[ $DRY_RUN == 1 ]]; then
    changed+=("$f")
    continue
  fi
  # 🛑 ERST SCHREIBEN, DANN MELDEN. Vorher wurde die Datei gezaehlt, BEVOR der
  # Schreibvorgang lief — eine schreibgeschuetzte Datei erzeugte "Permission
  # denied" auf stderr und stand danach trotzdem in der Erfolgsliste. Im Deploy
  # liest der Betreiber dann "5 Manifeste geschrieben", waehrend nichts
  # geschrieben wurde. Ein Werkzeug, das Fehlschlaege als Erfolg meldet, ist
  # schlimmer als eines, das gar nichts tut.
  if printf '%s\n' "$after" > "$f"; then
    changed+=("$f")
  else
    fehlgeschlagen+=("$f")
  fi
done < <(find "$DIR" -maxdepth 1 -name '*.yaml' -print0 | sort -z)

# 🛑 Erst berichten, was GELUNGEN ist, dann was fehlschlug. Bei einem
# Teilerfolg braucht der Betreiber beide Haelften: welche Manifeste schon
# stimmen und welche er von Hand nachziehen muss.
if [[ ${#changed[@]} -eq 0 && ${#fehlgeschlagen[@]} -eq 0 ]]; then
  echo "  Bildmarken schon auf dem Live-Stand — nichts zu tun."
  exit 0
fi

if [[ ${#changed[@]} -gt 0 ]]; then
  if [[ $DRY_RUN == 1 ]]; then was='[dry-run] wuerde schreiben:'; else was='geschrieben:'; fi
  printf '  %s %d Manifest(e) in %s:\n' "$was" "${#changed[@]}" "$DIR"
  for f in "${changed[@]}"; do printf '    %s\n' "$f"; done
fi

if [[ ${#fehlgeschlagen[@]} -gt 0 ]]; then
  printf 'ERROR: %d Manifest(e) NICHT geschrieben — von Hand nachziehen:\n' \
    "${#fehlgeschlagen[@]}" >&2
  for f in "${fehlgeschlagen[@]}"; do printf '    %s\n' "$f" >&2; done
  exit 1
fi

# Der Hinweis nennt das RICHTIGE Repo — die Manifeste einer privaten Instanz
# liegen nicht in dem Repo, aus dem deployt wurde.
repo_root="$(cd "$DIR" && git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -n "$repo_root" ]]; then
  echo
  echo "  🛑 NICHT committet — das bleibt Ihre Entscheidung:"
  echo "     cd $repo_root && git add -u k8s/ && git commit -m 'chore(k8s): Bildmarken auf den Live-Stand'"
fi
