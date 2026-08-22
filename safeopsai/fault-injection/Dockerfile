FROM python:3.12-slim

# docker CLI binary is needed for 'docker compose up -d backend' in bad_db_config.py
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user — Docker socket is bind-mounted at runtime (see compose)
RUN useradd -m injector && chown -R injector /app
USER injector

# Default: show help. Override CMD in docker compose run or docker run.
ENTRYPOINT ["python", "cli.py"]
CMD ["--help"]
