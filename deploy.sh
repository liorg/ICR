#!/usr/bin/env bash
set -euo pipefail

cd /opt/ICR

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

REGISTRY="${REGISTRY_HOST:-10.186.0.3}"
TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M%S)}"
STACK_NAME="${STACK_NAME:-scenario}"

echo "================================"
echo "Registry: ${REGISTRY}:5000"
echo "Tag:      ${TAG}"
echo "================================"

echo "Building spine..."
docker build \
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
