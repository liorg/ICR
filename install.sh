export REGISTRY_HOST=10.186.0.3
export SUPABASE_URL=https://umxgluptdopldndqjbvx.supabase.co
export SUPABASE_SERVICE_KEY=eyJhbG....
cd /opt/ICR
docker build -t ${REGISTRY_HOST}:5000/data-spine:latest ./spine/
docker build -t ${REGISTRY_HOST}:5000/provisioner:latest ./provisioner/
docker push ${REGISTRY_HOST}:5000/data-spine:latest
docker push ${REGISTRY_HOST}:5000/provisioner:latest
docker stack deploy -c docker-compose.yml scenario