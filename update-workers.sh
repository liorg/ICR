#!/usr/bin/env bash
# update-workers.sh — מעדכן את כל ה-Workers ל-image האחרון ב-Docker Hub, לפי SHA.
#
#   sudo ./update-workers.sh                 # ה-sha-* שמצביע לאותו digest כמו latest
#   sudo ./update-workers.sh sha-48f18d2     # תג מסוים
#   sudo ./update-workers.sh --dry-run       # רק מראה מה ייעשה
#   sudo ./update-workers.sh --force         # בלי בדיקת calls running
#
# env אופציונלי:
#   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  → בדיקת calls running לפני עדכון
#   WORKER_PORT (9000)                        → פורט ה-Worker לבדיקת /
#   SPINE_SERVICE (scenario_data-spine)       → ממנו קוראים ל-/ של כל Worker
set -euo pipefail

REPO="liorgr/worker-scenario-runtime"
WORKER_PORT="${WORKER_PORT:-9000}"
SPINE_SERVICE="${SPINE_SERVICE:-scenario_data-spine}"

TAG=""; DRY=0; FORCE=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --force)   FORCE=1 ;;
    sha-*)     TAG="$a" ;;
    *) echo "unknown arg: $a"; exit 2 ;;
  esac
done

# ── 1. תג + digest מ-Docker Hub ──────────────────────────────────────
JSON="$(curl -fsS "https://hub.docker.com/v2/repositories/${REPO}/tags?page_size=50&ordering=last_updated")"

read -r TAG DIGEST UPDATED < <(printf '%s' "$JSON" | python3 -c '
import json, sys
want = sys.argv[1]
tags = json.load(sys.stdin)["results"]
by = {t["name"]: t for t in tags}
if want:
    t = by.get(want)
    if not t: sys.exit(f"tag {want} not found")
else:
    latest = by.get("latest")
    if not latest: sys.exit("no latest tag")
    t = next((x for x in tags if x["name"].startswith("sha-") and x.get("digest") == latest.get("digest")), None)
    if not t: sys.exit("no sha-* tag matches latest digest")
print(t["name"], t.get("digest") or "", t.get("last_updated") or "")
' "$TAG")

[ -n "$TAG" ] && [ -n "$DIGEST" ] || { echo "✖ could not resolve tag/digest"; exit 1; }
IMAGE="${REPO}:${TAG}@${DIGEST}"
echo "▶ image : $IMAGE"
echo "  pushed: $UPDATED"

# ── 2. ה-services של ה-Workers ───────────────────────────────────────
mapfile -t SERVICES < <(docker service ls --format '{{.Name}} {{.Image}}' | awk -v r="$REPO" 'index($2, r) {print $1}')
[ "${#SERVICES[@]}" -gt 0 ] || { echo "✖ no worker services found"; exit 1; }
echo "▶ ${#SERVICES[@]} worker service(s):"
for S in "${SERVICES[@]}"; do
  echo "  $S  ← $(docker service inspect "$S" --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' | sed 's/@sha256:\(.\{12\}\).*/@sha256:\1…/')"
done

# ── 3. calls running ─────────────────────────────────────────────────
# ה-Worker מחזיק state בזיכרון — restart מאבד שיחה רצה.
if [ "$FORCE" -eq 0 ] && [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
  RUNNING="$(curl -fsS -o /dev/null -D - \
      "${SUPABASE_URL%/}/rest/v1/calls?status=eq.running&select=id" \
      -H "apikey: ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Authorization: Bearer ${SUPABASE_SERVICE_ROLE_KEY}" \
      -H "Prefer: count=exact" -H "Range: 0-0" \
    | awk -F/ 'tolower($0) ~ /^content-range:/ {gsub(/\r/,"",$2); print $2}')"
  echo "▶ running calls: ${RUNNING:-?}"
  if [ "${RUNNING:-0}" != "0" ] && [ "${RUNNING:-}" != "" ]; then
    echo "✖ יש calls running — המתן או הרץ עם --force"; exit 1
  fi
elif [ "$FORCE" -eq 0 ]; then
  echo "⚠ SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY לא מוגדרים — אין בדיקת calls running"
fi

[ "$DRY" -eq 1 ] && { echo "(dry-run) לא עודכן כלום"; exit 0; }

# ── 4. עדכון ─────────────────────────────────────────────────────────
for S in "${SERVICES[@]}"; do
  echo "== $S"
  docker service update --quiet --with-registry-auth --image "$IMAGE" "$S" >/dev/null
done

# ── 5. אימות ─────────────────────────────────────────────────────────
echo "▶ verify image"
FAIL=0
for S in "${SERVICES[@]}"; do
  CUR="$(docker service inspect "$S" --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}')"
  STATE="$(docker service ps "$S" --filter desired-state=running --format '{{.CurrentState}}' | head -1)"
  if [[ "$CUR" == *"$DIGEST"* ]]; then echo "  ✔ $S  $STATE"; else echo "  ✖ $S  $CUR"; FAIL=1; fi
done

C="$(docker ps -q -f label=com.docker.swarm.service.name="$SPINE_SERVICE" | head -1)"
if [ -n "$C" ]; then
  echo "▶ verify / (via $SPINE_SERVICE)"
  sleep 5
  for S in "${SERVICES[@]}"; do
    OUT="$(docker exec "$C" python3 -c "
import json, urllib.request
d = json.load(urllib.request.urlopen('http://$S:$WORKER_PORT/', timeout=5))
print(d.get('version'), d.get('status'))
" 2>/dev/null || echo "unreachable")"
    echo "  $S  → $OUT"
  done
else
  echo "⚠ $SPINE_SERVICE לא רץ על ה-node הזה — דילוג על בדיקת /"
fi

exit $FAIL
