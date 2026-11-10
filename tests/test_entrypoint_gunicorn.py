"""The serving entrypoint must preload the WSGI app.

`exact/wsgi.py` runs `require_trials_schema()` at import. Without `--preload`
every Gunicorn worker imports it itself, so a worker that respawns re-runs the
catalog inspection, and one failure there halts the whole server ("Worker
failed to boot", arbiter exit 3) instead of costing one worker. Measured with
the pinned Gunicorn: without --preload a worker killed after the check starts
failing takes the master down; with it the replacement worker serves 200.

Source-level and database-free: the entrypoint cannot run outside the image.
"""
import re
import shlex
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[1] / 'docker' / 'entrypoint.sh'


def _gunicorn_args():
    lines = [
        line for line in ENTRYPOINT.read_text().splitlines()
        if re.match(r'\s*exec\s+gunicorn\b', line)
    ]
    assert len(lines) == 1, lines
    return shlex.split(lines[0], comments=True)


def test_serving_gunicorn_preloads_the_app():
    args = _gunicorn_args()
    assert 'exact.wsgi' in args
    assert '--preload' in args
