.PHONY: install run stop reindex import-rip test deploy FORCE

# This machine's LAN IP, baked into the self-signed cert's SAN so phones/Chromecast on the
# LAN can reach it over HTTPS (needed for PWA install - browsers require a secure context).
# Auto-detected fresh on every `make run` (scripts/host_ip.py filters Windows' adapter list by
# name to skip Tailscale/NordVPN/other VPN adapters, even a full-tunnel one that's become the
# default route - a plain "which interface would 8.8.8.8 go out" probe gets fooled by those)
# rather than hardcoded, so a DHCP renewal or network change doesn't silently break LAN access
# until someone notices and updates this file. Still overridable (`make run HOST_IP=192.168.x.x`)
# since a command-line assignment always wins over the file regardless of the := below.
HOST_IP := $(shell uv run python scripts/host_ip.py)

install:
	uv sync

# Regenerates the cert only when HOST_IP actually changed since the last run (tracked in
# certs/.host_ip) - no more manual "delete certs/ first" step when the LAN IP changes.
certs/.host_ip: FORCE
	@mkdir -p certs
	@if [ ! -f certs/.host_ip ] || [ "$$(cat certs/.host_ip)" != "$(HOST_IP)" ]; then \
		echo "$(HOST_IP)" > certs/.host_ip; \
	fi

certs/cert.pem: certs/.host_ip
	MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes \
		-keyout certs/key.pem -out certs/cert.pem -days 825 \
		-subj "/CN=pan.local" \
		-addext "subjectAltName=IP:$(HOST_IP),DNS:localhost,IP:127.0.0.1"

# Chromecast's receiver hardware fetches /stream/{id} directly and does real TLS validation -
# it can't click through a self-signed cert warning the way a browser can, so casting needs a
# plain-HTTP path. Port 8080 here is that path (whole app, but only /stream is actually used
# over it); the main app stays on HTTPS:8000 for the PWA/secure-context requirement.
run: certs/cert.pem
	uv run uvicorn backend.app:app --host 0.0.0.0 --port 8080 & \
	trap 'kill %1' EXIT; \
	uv run uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000 \
		--ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem

stop:
	powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/stop_server.ps1

reindex:
	uv run python -m scripts.reindex

# Run this after EAC finishes ripping a CD to Z:\music (SHARED_DRIVE_PATH) - tells the
# already-running `make run` server to pick up the new files now, instead of waiting for the
# next scheduled reindex.
import-rip:
	uv run python -m scripts.import_rip

test:
	uv run pytest

# Pushes code/config to the NAS and rebuilds - pan.db/.env on the NAS are live state, never
# touched (see README "Updating the deployed app"). Assumes `pan-nas` is set up in your own
# ~/.ssh/config (see README "SSH access to the NAS"). Refuses to run with uncommitted changes so
# you can't accidentally deploy something you haven't committed (and can always tell exactly
# what's live from VERSION/`/version` afterward).
#
# Deliberately still a command you run by hand, not CI/CD triggered on push - see README
# "Driving deploys over SSH without broad docker access" for why this stays manual. Running the
# extraction over a non-interactive `ssh host 'cmd'` (rather than an interactive login shell) also
# sidesteps this NAS's /etc/profile setting `umask 077` for login shells, which produced
# unreadable 0600 files the one time this was done by hand over an interactive session instead.
deploy:
	git diff --quiet HEAD -- backend ui scripts Dockerfile docker-compose.yml pyproject.toml uv.lock || \
		{ echo "Uncommitted changes under backend/ui/scripts/Dockerfile/docker-compose.yml/pyproject.toml/uv.lock - commit first."; exit 1; }
	git rev-parse --short HEAD > VERSION
	tar -czf /tmp/pan-deploy.tar.gz backend ui scripts Dockerfile docker-compose.yml pyproject.toml uv.lock VERSION
	scp -O /tmp/pan-deploy.tar.gz pan-nas:/tmp/pan-deploy.tar.gz
	ssh pan-nas 'cd /volume1/docker/pan && tar -xzf /tmp/pan-deploy.tar.gz && rm /tmp/pan-deploy.tar.gz'
	ssh pan-nas 'sudo /volume1/@appstore/ContainerManager/usr/bin/docker compose -f /volume1/docker/pan/docker-compose.yml build --no-cache'
	ssh pan-nas 'sudo /volume1/@appstore/ContainerManager/usr/bin/docker compose -f /volume1/docker/pan/docker-compose.yml up -d --force-recreate'
	ssh pan-nas 'sudo /volume1/@appstore/ContainerManager/usr/bin/docker compose -f /volume1/docker/pan/docker-compose.yml logs --tail=200 pan'
	@echo "Deployed. Verify at https://pan.tail8f9049.ts.net/version"

FORCE:
