.PHONY: install run reindex test

# This machine's LAN IP, baked into the self-signed cert's SAN so phones/Chromecast on the
# LAN can reach it over HTTPS (needed for PWA install - browsers require a secure context).
# Override if it changes: `make run HOST_IP=192.168.x.x` (delete certs/ first to regenerate).
HOST_IP ?= 192.168.4.45

install:
	uv sync

certs/cert.pem:
	mkdir -p certs
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
