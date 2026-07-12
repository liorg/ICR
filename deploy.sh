#!/usr/bin/env bash
set -euo pipefail
cd /opt/ICR

# טעינת משתני הסביבה
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

REGISTRY="${REGISTRY_HOST:-10.186.0.3}"
TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
STACK_NAME="scenario"

echo "================================"
echo "Registry: ${REGISTRY}:5000"
echo "Tag:      ${TAG}"
echo "Stack:    ${STACK_NAME}"
echo "================================"

echo "Building spine..."
docker build -t "${REGISTRY}:5000/data-spine:${TAG}" ./spine/

echo "Pushing spine..."
docker push "${REGISTRY}:5000/data-spine:${TAG}"

echo "Building provisioner..."
docker build -t "${REGISTRY}:5000/provisioner:${TAG}" ./provisioner/

echo "Pushing provisioner..."
docker push "${REGISTRY}:5000/provisioner:${TAG}"

echo "Deploying Docker Stack..."
docker stack deploy \
  --with-registry-auth \
  --resolve-image always \
  -c docker-compose.yml \
  "${STACK_NAME}"

echo "Updating services with new image..."
docker service update \
  --image "${REGISTRY}:5000/data-spine:${TAG}" \
  "${STACK_NAME}_data-spine"

docker service update \
  --image "${REGISTRY}:5000/provisioner:${TAG}" \
  "${STACK_NAME}_provisioner"

echo "================================"
echo "Deployed — tag: ${TAG}"
echo "================================"
docker stack services "${STACK_NAME}"
