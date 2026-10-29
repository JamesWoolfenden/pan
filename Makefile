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

# Pulls the image CI built for a commit (.github/workflows/ci.yml) and restarts on it - nothing is
# built on the NAS. pan.db/.env there are live state, never touched (see README "Updating the
# deployed app"). Assumes `pan-nas` is set up in your own ~/.ssh/config (see README "SSH access to
# the NAS").
#
# TAG defaults to the current commit, which only exists once it's pushed and CI has finished -
# the pull fails loudly otherwise rather than quietly redeploying the old image. Roll back with
# `make deploy TAG=<older full sha>` (`git log` for the SHA). Only docker-compose.yml is shipped,
# with the tag baked in, since the code itself now lives in the image.
#
# Deliberately still a command you run by hand, not CI/CD triggered on push - see README
# "Driving deploys over SSH without broad docker access" for why this stays manual. Copying over
# a non-interactive `ssh`/`scp` (rather than an interactive login shell) also sidesteps this NAS's
# /etc/profile setting `umask 077` for login shells, which produced unreadable 0600 files the one
# time this was done by hand over an interactive session instead.
TAG ?= $(shell git rev-parse HEAD)
deploy:
	git diff --quiet HEAD -- docker-compose.yml || \
		{ echo "Uncommitted changes to docker-compose.yml - commit first."; exit 1; }
	@echo "Deploying image $(TAG) - committed and pushed code only, not your working tree."
	digest=$$(uv run python scripts/image_digest.py $(TAG)) && \
		sed -E "s#ghcr.io/jameswoolfenden/pan:[^[:space:]]+#ghcr.io/jameswoolfenden/pan:$(TAG)@$$digest#" docker-compose.yml > pan-deploy-compose.yml
	scp -O pan-deploy-compose.yml pan-nas:/volume1/docker/pan/docker-compose.yml
	rm pan-deploy-compose.yml
	ssh pan-nas 'sudo /volume1/@appstore/ContainerManager/usr/bin/docker compose -f /volume1/docker/pan/docker-compose.yml pull pan'
	ssh pan-nas 'sudo /volume1/@appstore/ContainerManager/usr/bin/docker compose -f /volume1/docker/pan/docker-compose.yml up -d --force-recreate'
	ssh pan-nas 'sudo /volume1/@appstore/ContainerManager/usr/bin/docker compose -f /volume1/docker/pan/docker-compose.yml logs --tail=200 pan'
	@echo "Deployed. Verify at https://pan.tail8f9049.ts.net/version"

FORCE:
