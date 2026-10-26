#!/bin/sh
# The gateway follows a service to its new IP after a restart (K6 found 502s until nginx was reloaded).
# Docker hands a restarted container the lowest free address, usually its old one, so while audit-service
# is stopped a placeholder container takes that address; audit-service then comes back on a new one,
# and the gateway must reach it again within 30 s.
# Usage (dev stack running): sh nginx/tests/upstream_reresolve.sh   (GATEWAY and COMPOSE optional)
G=${GATEWAY:-http://localhost}
COMPOSE=${COMPOSE:-docker compose}

ip() { docker inspect "$($COMPOSE ps -q "$1")" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'; }

before_audit=$(ip audit-service)
net=$(docker inspect "$($COMPOSE ps -q audit-service)" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
holders=""; after_audit=$before_audit; try=0
# Each placeholder takes the lowest free address; a few may be needed before it is audit-service's
while [ "$after_audit" = "$before_audit" ] && [ $try -lt 5 ]; do
  $COMPOSE stop audit-service >/dev/null 2>&1
  holders="$holders $(docker run -d --rm --network "$net" --entrypoint sleep redis:7-alpine 120)"
  $COMPOSE start audit-service >/dev/null 2>&1
  after_audit=$(ip audit-service); try=$((try + 1))
done
docker rm -f $holders >/dev/null 2>&1
echo "audit-service: $before_audit -> $after_audit"
[ "$before_audit" = "$after_audit" ] && { echo "SKIP the IP never changed, nothing to prove"; exit 2; }

i=0
while [ $i -lt 30 ]; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$G/api/audit/health")
  [ "$code" = 200 ] && { echo "ok   gateway reaches audit-service after ${i}s"; exit 0; }
  i=$((i + 1)); sleep 1
done
echo "FAIL gateway still gets $code from audit-service after 30 s"
exit 1
