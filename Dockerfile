FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONHASHSEED=random PYTHONDONTWRITEBYTECODE=1 PORT=8000

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

EXPOSE 8000

# Fixing since it was broken at /app
CMD ["/docker/entrypoint.sh"]
