#!/bin/bash
cd /opt/ICR
REGISTRY=${REGISTRY_HOST:-10.186.0.3}

echo "Building spine..."
docker build -t ${REGISTRY}:5000/data-spine:latest ./spine/
docker push ${REGISTRY}:5000/data-spine:latest

echo "Building provisioner..."
docker build -t ${REGISTRY}:5000/provisioner:latest ./provisioner/
docker push ${REGISTRY}:5000/provisioner:latest

echo "Updating services..."
docker service update --image ${REGISTRY}:5000/data-spine:latest --force scenario_data-spine
docker service update --image ${REGISTRY}:5000/provisioner:latest --force scenario_provisioner

echo "Done"
docker stack services scenario
