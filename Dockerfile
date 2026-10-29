FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

# holden:ignore:HLD_DF_020
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app

COPY --from=ghcr.io/astral-sh/uv:latest@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /uvx /bin/

WORKDIR /app
RUN chown -R app:app /app
USER app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
# uv run would otherwise re-sync at every start (CMD or a terminal session), installing the
# dev group from the network - use only what the build installed.
ENV UV_NO_SYNC=1

COPY backend ./backend
COPY scripts ./scripts
COPY ui ./ui
COPY VERSION ./VERSION

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
