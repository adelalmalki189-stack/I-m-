#!/bin/sh
# Used by the Release workflow: copy the deploy files to the server and run deploy.sh there.
# Needs: SSH_KEY, HOST, USER, DEPLOY_PATH, TAG. The server keeps its own .env (never copied).
set -eu
mkdir -p ~/.ssh
printf '%s\n' "$SSH_KEY" > ~/.ssh/deploy_key
chmod 600 ~/.ssh/deploy_key
ssh-keyscan -H "$HOST" >> ~/.ssh/known_hosts 2>/dev/null
SSH="ssh -i $HOME/.ssh/deploy_key $USER@$HOST"
scp -i ~/.ssh/deploy_key deploy/docker-compose.prod.yml deploy/Caddyfile deploy/deploy.sh deploy/backup.sh deploy/restore.sh \
	"$USER@$HOST:$DEPLOY_PATH/"
$SSH "cd '$DEPLOY_PATH' && chmod +x deploy.sh backup.sh restore.sh && ./deploy.sh '$TAG'"
