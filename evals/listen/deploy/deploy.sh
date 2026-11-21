#!/usr/bin/env bash
# Deploy the listening-feedback server, or publish one page to it.
#
#   HOST=ubuntu@<server> SITE=<domain> evals/listen/deploy/deploy.sh install
#   HOST=ubuntu@<server> evals/listen/deploy/deploy.sh publish <built-page-dir> <slug>
#   HOST=ubuntu@<server> evals/listen/deploy/deploy.sh feedback <slug>
#   HOST=ubuntu@<server> evals/listen/deploy/deploy.sh reports <slug>
#   HOST=ubuntu@<server> evals/listen/deploy/deploy.sh update     # ship server.py only, restart
#
# The server address is not kept in the repository. Ask the architect.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
: "${HOST:?set HOST=user@server}"

case "${1:-}" in
install)
	: "${SITE:?set SITE=<domain>}"
	tar -C "$here/../.." -cf - listen/__init__.py listen/server.py \
		| ssh "$HOST" 'sudo mkdir -p /srv/amadeus-listen/app && sudo tar -C /srv/amadeus-listen/app -xf -'
	scp -q "$here/amadeus-listen.service" "$here/Caddyfile" "$HOST:/tmp/"
	ssh "$HOST" SITE="$SITE" 'bash -s' <<'REMOTE'
set -euo pipefail
id amadeus-listen >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin amadeus-listen
sudo mkdir -p /srv/amadeus-listen/pages /srv/amadeus-listen/data
sudo chown -R root:root /srv/amadeus-listen/app /srv/amadeus-listen/pages
sudo chown -R amadeus-listen:amadeus-listen /srv/amadeus-listen/data
sudo chmod 750 /srv/amadeus-listen/data
sudo install -m 644 /tmp/amadeus-listen.service /etc/systemd/system/amadeus-listen.service
if ! command -v caddy >/dev/null; then
	sudo apt-get update -qq
	sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg
	curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
	sudo apt-get update -qq
	sudo apt-get install -y -qq caddy
fi
sudo install -m 644 /tmp/Caddyfile /etc/caddy/Caddyfile
echo "SITE=$SITE" | sudo tee /etc/default/caddy >/dev/null
sudo mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/default/caddy\n' | sudo tee /etc/systemd/system/caddy.service.d/site.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now amadeus-listen
sudo systemctl restart amadeus-listen
sudo systemctl enable caddy
sudo systemctl restart caddy
systemctl is-active amadeus-listen caddy
REMOTE
	;;
publish)
	dir="${2:?built page dir}" slug="${3:?slug}"
	[[ "$slug" =~ ^[A-Za-z0-9_-]{16,64}$ ]] || { echo "slug must be 16-64 of [A-Za-z0-9_-]" >&2; exit 1; }
	tar -C "$dir" -cf - . | ssh "$HOST" "sudo rm -rf /srv/amadeus-listen/pages/$slug.new && sudo mkdir -p /srv/amadeus-listen/pages/$slug.new && sudo tar -C /srv/amadeus-listen/pages/$slug.new -xf - && sudo rm -rf /srv/amadeus-listen/pages/$slug && sudo mv /srv/amadeus-listen/pages/$slug.new /srv/amadeus-listen/pages/$slug"
	echo "published /l/$slug/"
	;;
feedback)
	slug="${2:?slug}"
	ssh "$HOST" "sudo cat /srv/amadeus-listen/data/$slug/feedback.json 2>/dev/null || echo '{}'"
	;;
reports)
	slug="${2:?slug}"
	ssh "$HOST" "sudo cat /srv/amadeus-listen/data/$slug/reports.jsonl 2>/dev/null || true"
	;;
update)
	tar -C "$here/../.." -cf - listen/__init__.py listen/server.py \
		| ssh "$HOST" 'sudo tar -C /srv/amadeus-listen/app -xf - && sudo systemctl restart amadeus-listen && systemctl is-active amadeus-listen'
	;;
*)
	sed -n '2,10p' "$0"
	exit 1
	;;
esac
