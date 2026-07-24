#!/usr/bin/env bash
set -e

case "${RUN_MIGRATIONS:-true}" in
    0|false|no|off|FALSE|False)
        echo "Skipping migrations (RUN_MIGRATIONS=${RUN_MIGRATIONS}); expecting a separate migrate job."
        ;;
    *)
        echo "Running migrations..."
        python manage.py migrate --run-syncdb
        # Concept-graph cache table (#234) — idempotent. Tolerate a concurrent-start
        # race (two containers both passing the exists-check, one losing the CREATE):
        # migrate above already proved the DB reachable, so the only failure left here
        # is a harmless duplicate-table, which must not abort startup under `set -e`.
        python manage.py createcachetable --database=default \
            || echo "createcachetable: table already exists or concurrent start; continuing"
        ;;
esac

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
