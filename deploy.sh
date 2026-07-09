#!/bin/bash
cd /opt/ICR
export $(cat .env | xargs)
docker stack deploy -c docker-compose.yml scenario