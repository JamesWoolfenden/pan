.PHONY: install run reindex test FORCE

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

reindex:
	uv run python -m scripts.reindex

test:
	uv run pytest

FORCE:
