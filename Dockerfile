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

# requirements.txt installs the converged matcher as an editable local package
# (`-e ./matcher_app`), so that directory must exist at pip time. Copy it in
# before the install (cwd is WORKDIR /app, so `./matcher_app` resolves); the
# later `COPY . /app` still brings the full tree. Without this the build fails
# with "./matcher_app is not a valid editable requirement" (#313).
COPY --chown=app:app matcher_app /app/matcher_app

RUN python3 -m pip install --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

RUN python manage.py collectstatic --no-input

EXPOSE 8080

CMD ["/app/docker/entrypoint.sh"]
