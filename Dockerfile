# Federation remote. The portal loads exact_remote/TrialMatches straight from
# this service, so the built remote has to ship inside the image — whitenoise
# serves it from WHITENOISE_ROOT.
FROM node:24-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

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
COPY --from=frontend --chown=app:app /app/frontend/dist/remote /app/frontend/dist/remote

RUN python manage.py collectstatic --no-input

EXPOSE 8080

CMD ["/app/docker/entrypoint.sh"]
