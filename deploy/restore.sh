#!/bin/sh
# Restore a backup into the running stack. DESTROYS the current data.
#   ./restore.sh db-YYYYmmdd-HHMMSS.dump [uploads-YYYYmmdd-HHMMSS.tar.gz]
# Run from the deploy directory on the server. Files are read from the `backups` volume.
set -eu
DUMP=${1:?usage: restore.sh db-....dump [uploads-....tar.gz]}
UPLOADS=${2:-}
COMPOSE="docker compose -f docker-compose.prod.yml ${COMPOSE_EXTRA:-} --env-file .env"   # COMPOSE_EXTRA="-f override.yml" to add overrides

printf 'This replaces ALL current data with %s. Type RESTORE to continue: ' "$DUMP"
read -r answer
[ "$answer" = "RESTORE" ] || { echo "aborted"; exit 1; }

$COMPOSE stop app worker
$COMPOSE exec -T backup pg_restore --clean --if-exists --no-owner --dbname="${POSTGRES_DB:-bud}" "/backups/$DUMP"
if [ -n "$UPLOADS" ]; then
	PROJECT=${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}
	$COMPOSE run --rm --no-deps -v "${PROJECT}_uploads:/restore" --entrypoint sh backup \
		-c "rm -rf /restore/* && tar -xzf /backups/$UPLOADS -C /restore"
fi
$COMPOSE run --rm migrate
$COMPOSE start app worker
echo "restored $DUMP${UPLOADS:+ and $UPLOADS}"
