#!/bin/sh
# Deploy a release on this server:  ./deploy.sh <image-tag>
# pull → backup → migrate → restart app+worker → wait for /health/ready → on failure restart the previous tag.
# Note: a failed release's migrations are NOT rolled back automatically; migrations are written
# to be additive, and the previous release keeps working on the newer schema.
set -eu
TAG=${1:?usage: deploy.sh <image-tag>}
COMPOSE="docker compose -f docker-compose.prod.yml ${COMPOSE_EXTRA:-} --env-file .env"   # COMPOSE_EXTRA="-f override.yml" to add overrides
PREVIOUS=$(cat .deployed-version 2>/dev/null || echo "")

export BUD_VERSION="$TAG"
[ "${SKIP_PULL:-}" = 1 ] || $COMPOSE pull app worker migrate   # SKIP_PULL=1 for a locally built image
$COMPOSE up -d db backup
if [ -n "$PREVIOUS" ]; then
	$COMPOSE exec -T backup sh /usr/local/bin/backup.sh --once   # safety copy before migrating
fi
$COMPOSE run --rm migrate
healthy() {
	$COMPOSE exec -T app python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/ready',timeout=3).status==200 else 1)" >/dev/null 2>&1
}

# `up` itself can fail (Caddy waits for a healthy app), so never let `set -e` skip the rollback.
if $COMPOSE up -d app worker caddy; then
	for _ in $(seq 1 "${HEALTH_TRIES:-30}"); do
		if healthy; then
			echo "$TAG" > .deployed-version
			echo "deployed $TAG"
			exit 0
		fi
		sleep 5
	done
fi

echo "release $TAG is not healthy" >&2
if [ -n "$PREVIOUS" ]; then
	echo "rolling back to $PREVIOUS" >&2
	export BUD_VERSION="$PREVIOUS"
	$COMPOSE up -d app worker caddy || true
	for _ in $(seq 1 "${HEALTH_TRIES:-30}"); do
		healthy && { echo "rolled back to $PREVIOUS"; break; }
		sleep 5
	done
fi
exit 1
