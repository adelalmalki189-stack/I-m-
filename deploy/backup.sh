#!/bin/sh
# Database + uploaded documents backup.
#   backup.sh --once     take one backup now
#   backup.sh --daily    loop: one backup a day at BACKUP_HOUR_UTC, keep BACKUP_RETENTION_DAYS
# Files land in /backups (a Docker volume). Copy them off the server too — see docs/DEPLOYMENT.md.
set -eu

take_backup() {
	stamp=$(date -u +%Y%m%d-%H%M%S)
	pg_dump --format=custom --file="/backups/db-$stamp.dump.partial"
	mv "/backups/db-$stamp.dump.partial" "/backups/db-$stamp.dump"
	tar -czf "/backups/uploads-$stamp.tar.gz.partial" -C /uploads .
	mv "/backups/uploads-$stamp.tar.gz.partial" "/backups/uploads-$stamp.tar.gz"
	find /backups -name 'db-*.dump' -mtime +"${BACKUP_RETENTION_DAYS:-14}" -delete
	find /backups -name 'uploads-*.tar.gz' -mtime +"${BACKUP_RETENTION_DAYS:-14}" -delete
	echo "backup ok: db-$stamp.dump uploads-$stamp.tar.gz"
}

case "${1:---once}" in
--once) take_backup ;;
--daily)
	while true; do
		hour=$(date -u +%H)
		if [ "$hour" -eq "${BACKUP_HOUR_UTC:-0}" ] && [ ! -f "/backups/.done-$(date -u +%Y%m%d)" ]; then
			take_backup && touch "/backups/.done-$(date -u +%Y%m%d)"
			find /backups -name '.done-*' -mtime +2 -delete
		fi
		sleep 300
	done
	;;
*) echo "usage: backup.sh --once|--daily" >&2; exit 2 ;;
esac
