#!/usr/bin/env bash
set -e

echo "Running migrations..."
python manage.py migrate --run-syncdb

bash /app/docker/init_trials_db.sh
bash /app/docker/init_patients_db.sh

cat <<'EOF'

============================================================
  Server is starting. To create a superuser and API token:

    docker compose exec exact python manage.py createsuperuser
    docker compose exec exact python manage.py drf_create_token <username>
============================================================

EOF

echo "Starting gunicorn..."
exec gunicorn exact.wsgi --workers 4 --timeout 300 --log-file - --bind "0.0.0.0:${PORT:-8080}"
