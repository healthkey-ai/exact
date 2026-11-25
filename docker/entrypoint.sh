#!/usr/bin/env bash
set -e

python manage.py check_trials_schema --configuration-only

case "${RUN_MIGRATIONS:-true}" in
    0|false|no|off|FALSE|False)
        echo "Skipping migrations (RUN_MIGRATIONS=${RUN_MIGRATIONS}); expecting a separate migrate job."
        ;;
    *)
        echo "Running migrations..."
        python manage.py migrate --run-syncdb
        ;;
esac

bash /app/docker/init_trials_db.sh
bash /app/docker/init_patients_db.sh

python manage.py check_trials_schema

cat <<'EOF'

============================================================
  Server is starting. To create a superuser and API token:

    docker compose exec exact python manage.py createsuperuser
    docker compose exec exact python manage.py drf_create_token <username>
============================================================

EOF

echo "Starting gunicorn..."
# --preload: import exact.wsgi (and its require_trials_schema() gate) once, in
# the master, before forking. Without it every worker imports the app itself,
# so a worker that respawns after a crash or a --timeout kill re-runs the
# catalog inspection, and if that one fails (e.g. a transient connection
# error) gunicorn halts the whole server with "Worker failed to boot" instead
# of losing one worker. The inspector uses and closes its own connection, so
# no database socket is inherited by the workers. The gate certifies the
# catalog before traffic only; drift after boot is out of its scope (see
# docs/trials-catalog-schema.md) and is caught at the next start.
exec gunicorn exact.wsgi --preload --workers 4 --timeout 300 --log-file - --bind "0.0.0.0:${PORT:-8080}"
