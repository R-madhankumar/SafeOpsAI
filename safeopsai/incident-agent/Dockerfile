FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the package and the migration file it needs at runtime
COPY incident_agent/ ./incident_agent/

# The migration SQL lives two levels up in the repo; in the container
# we place it at /app/database/ so db.py can find it via its Path(__file__)
# relative resolution (which resolves to /app/database/migrate_step3.sql).
# The compose volume mount handles this at runtime; we create the dir here
# so the image is self-contained even if the mount is absent.
RUN mkdir -p /app/database

RUN useradd -m agentuser && chown -R agentuser /app
USER agentuser

EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["python", "-m", "incident_agent.main"]
