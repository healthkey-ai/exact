FROM python:3.12-slim

# Cloud Run injects $PORT and routes to the container's configured port
# (8080 in the house cloud_run module). gunicorn binds $PORT below.
ENV PYTHONUNBUFFERED=1 PYTHONHASHSEED=random PYTHONDONTWRITEBYTECODE=1 PORT=8080

RUN apt-get update \
    && apt-get -y --no-install-recommends install \
    g++ \
    libpq-dev \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libgeos-c1v5 \
    postgresql-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY requirements.txt /tmp/requirements.txt

RUN useradd -m -d /app -u 1000 app
USER app
WORKDIR /app
ENV PATH="$PATH:/app/.local/bin"

RUN python3 -m pip install --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

RUN python manage.py collectstatic --no-input

EXPOSE 8080

# Cloud Run serving command: gunicorn only. Migrations run in the dedicated
# `exact-staging-migrate` Cloud Run job (command overridden by the house
# cloud_run module to `manage.py migrate`), NOT in the serving container.
# Local dev uses docker/entrypoint.sh instead, pinned via docker-compose's
# own `entrypoint:` override — so that path (migrate + seed) is unaffected.
CMD ["sh", "-c", "exec gunicorn exact.wsgi --workers ${GUNICORN_WORKERS:-4} --timeout ${GUNICORN_TIMEOUT:-300} --log-file - --bind 0.0.0.0:${PORT}"]
