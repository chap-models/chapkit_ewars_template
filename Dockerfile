# R-INLA is amd64-only.
ARG BASE_PLATFORM=linux/amd64

FROM --platform=${BASE_PLATFORM} ghcr.io/dhis2-chap/chapkit-r-inla:latest

ENV UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY scripts/ ./scripts/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
