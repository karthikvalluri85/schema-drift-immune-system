#!/usr/bin/env bash
# Second step on the Droplet (run once as root): writes env + systemd units + Caddyfile.
set -euo pipefail
REPO=/opt/sdis/repo
IP=$(curl -s http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address)
HOST_SUFFIX="${IP}.sslip.io"
ENV=/etc/sdis/sdis.env

if [ ! -f "$ENV" ]; then
  cp "$REPO/.env.example" "$ENV"
  {
    echo "SDIS_REPO_DIR=$REPO"
    echo "SNOWFLAKE_PRIVATE_KEY_PATH=/etc/sdis/keys/sdis_agent.p8"
    echo "SDIS_API_TOKEN=$(openssl rand -hex 24)"
    echo "GITHUB_WEBHOOK_SECRET=$(openssl rand -hex 24)"
    echo "JIRA_WEBHOOK_SECRET=$(openssl rand -hex 24)"
    echo "BETTER_AUTH_SECRET=$(openssl rand -hex 32)"
    echo "PAPERCLIP_HOME=/var/lib/paperclip"
    echo "PAPERCLIP_DEPLOYMENT_MODE=authenticated"
    echo "PAPERCLIP_DEPLOYMENT_EXPOSURE=public"
    echo "PAPERCLIP_PUBLIC_URL=https://paperclip.${HOST_SUFFIX}"
    echo "PAPERCLIP_NO_BROWSER=1"
  } >> "$ENV"
  chown sdis:sdis "$ENV" && chmod 600 "$ENV"
fi

install -m 644 "$REPO/deploy/digitalocean/paperclip.service"      /etc/systemd/system/
install -m 644 "$REPO/deploy/digitalocean/sdis-api.service"       /etc/systemd/system/
install -m 644 "$REPO/deploy/digitalocean/sdis-dashboard.service" /etc/systemd/system/

read -r -p "Dashboard username [karthik]: " DASH_USER; DASH_USER=${DASH_USER:-karthik}
DASH_HASH=$(caddy hash-password)
sed -e "s/{HOST_SUFFIX}/${HOST_SUFFIX}/g" -e "s/{DASH_USER}/${DASH_USER}/g" -e "s#{DASH_HASH}#${DASH_HASH}#g" \
    "$REPO/deploy/digitalocean/Caddyfile" > /etc/caddy/Caddyfile

systemctl daemon-reload
systemctl enable --now paperclip sdis-api sdis-dashboard
systemctl reload caddy

cat <<MSG

✅ Done. Open:
   Paperclip  https://paperclip.${HOST_SUFFIX}
   API docs   https://api.${HOST_SUFFIX}/docs
   Dashboard  https://dash.${HOST_SUFFIX}   (user: ${DASH_USER})

Next:
 1. Copy your Snowflake private key:  scp sdis_agent.p8 root@${IP}:/etc/sdis/keys/ && chown sdis /etc/sdis/keys/*
 2. Fill SNOWFLAKE_ACCOUNT, GITHUB_TOKEN, JIRA_EMAIL, JIRA_API_TOKEN in ${ENV}; then: systemctl restart sdis-api sdis-dashboard paperclip
 3. Import the company:  sudo -u sdis npx paperclipai company import ${REPO}/company --target new --yes
 4. GitHub webhook → https://api.${HOST_SUFFIX}/webhooks/github  (secret: GITHUB_WEBHOOK_SECRET in ${ENV})
 5. Jira webhook   → https://api.${HOST_SUFFIX}/webhooks/jira?secret=<JIRA_WEBHOOK_SECRET>
MSG
