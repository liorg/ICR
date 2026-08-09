
#!/usr/bin/env bash
set -euo pipefail

cd /opt/ICR

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

REGISTRY="${REGISTRY_HOST:-10.186.0.3}"
STACK_NAME="${STACK_NAME:-scenario}"

# ה-CI מעביר BUILD_TAG=${{ github.sha }}. בהרצה ידנית נופלים ל-timestamp,
# כדי שהסקריפט יעבוד גם בלי משתנה סביבה.
#
# חשוב שהתג ישתנה בכל פריסה: Swarm לא מושך image מחדש כשהתג זהה, ואז
# service update מריץ בדיוק את הקוד הישן.
TAG="${BUILD_TAG:-$(date +%Y%m%d%H%M%S)}"

echo "================================"
echo "Registry: ${REGISTRY}:5000"
echo "Tag:      ${TAG}"
echo "================================"

# --build-arg רק ל-spine: רק ה-Dockerfile שלו מגדיר ARG BUILD_TAG
# (בשביל GET /version). העברה לאחרים מייצרת אזהרת "not consumed".
echo "Building spine..."
docker build \
  --build-arg BUILD_TAG="${TAG}" \
  -t "${REGISTRY}:5000/data-spine:${TAG}" \
  -t "${REGISTRY}:5000/data-spine:latest" \
  ./spine/

docker push "${REGISTRY}:5000/data-spine:${TAG}"
docker push "${REGISTRY}:5000/data-spine:latest"

echo "Building provisioner..."
docker build \
  -t "${REGISTRY}:5000/provisioner:${TAG}" \
  -t "${REGISTRY}:5000/provisioner:latest" \
  ./provisioner/

docker push "${REGISTRY}:5000/provisioner:${TAG}"
docker push "${REGISTRY}:5000/provisioner:latest"

echo "Building scheduler..."
docker build \
  -t "${REGISTRY}:5000/scheduler:${TAG}" \
  -t "${REGISTRY}:5000/scheduler:latest" \
  ./scheduler/

docker push "${REGISTRY}:5000/scheduler:${TAG}"
docker push "${REGISTRY}:5000/scheduler:latest"

echo "Deploying stack..."
IMAGE_TAG="${TAG}" \
REGISTRY_HOST="${REGISTRY}" \
docker stack deploy \
  --with-registry-auth \
  --resolve-image always \
  -c docker-compose.yml \
  "${STACK_NAME}"

echo "Waiting for services..."
sleep 10

echo "================================"
echo "Deployed — tag: ${TAG}"
echo "================================"

docker stack services "${STACK_NAME}"

# אימות שהקוד שרץ הוא הקוד שנפרס. REPLICAS ב-0/1 למעלה מספיק כדי
# לדעת שמשהו קורס, אבל /version מאשר שגם הקוד עצמו התחלף.
echo ""
echo "Spine version:"
curl -sf --max-time 5 "http://${REGISTRY}:8001/version" || \
    echo "  (spine not responding yet — check: docker service logs ${STACK_NAME}_data-spine)"
echo ""
