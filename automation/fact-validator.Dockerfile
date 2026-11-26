# Build from the repository root. Pin this base image to an approved digest in production.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY automation/fact_validator.py /app/fact_validator.py
COPY automation/fact_check_backend.py /app/fact_check_backend.py

# The launcher uses a rootless Docker daemon and UID 0 inside its user namespace
# to read the run files (mounted read-only). Rootless maps that identity to the
# unprivileged daemon owner on the host, not to host root.
USER 65532:65532
ENTRYPOINT ["python3", "/app/fact_validator.py"]
