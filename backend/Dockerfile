FROM python:3.11-slim

# SECURITY-2D-3C2: the app runs non-root and the source tree is root-owned/read-only to it, so Python must
# not try to write .pyc caches into it at runtime.
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source is copied AS ROOT (no --chown): /app and the installed site-packages stay root-owned and are only
# readable/executable by the app user — the application can never rewrite its own code.
COPY . .

# SECURITY-2D-3C2 — run as a fixed non-root user (UID/GID 10001). Writable allowlist is exactly:
#   /app/uploads  — the only runtime write path (CSV import staging; ephemeral, no volume)
#   /tmp          — the standard world-writable temp (unchanged)
# The source tree stays root-owned (it is never recursively re-owned or made world-writable). Migrations
# write only to PostgreSQL and read the root-owned alembic files, so non-root needs no source write.
RUN groupadd -r -g 10001 app \
    && useradd -r -u 10001 -g app -s /usr/sbin/nologin app \
    && mkdir -p /app/uploads \
    && chown 10001:10001 /app/uploads
USER 10001:10001

EXPOSE 8000

# Apply DB migrations (prod skips create_all) then start the server.
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
