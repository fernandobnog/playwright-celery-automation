#!/bin/bash
set -e

echo "=== [Omni-Flow Container Initializing] ==="
echo "Service Type: ${SERVICE_TYPE:-default}"
echo "Hostname: $(hostname)"

export PYTHONPATH="/app:${PYTHONPATH}"
cd /app

# Wait for Redis broker to be ready
wait_for_redis() {
  echo "Waiting for Redis broker at ${REDIS_URL:-redis://redis:6379/0}..."
  python - <<EOF
import sys, time
from urllib.parse import urlparse
import redis

url = "${REDIS_URL:-redis://redis:6379/0}"
max_retries = 30
for i in range(max_retries):
    try:
        r = redis.Redis.from_url(url, socket_connect_timeout=2)
        if r.ping():
            print("Successfully connected to Redis!")
            sys.exit(0)
    except Exception as e:
        print(f"Waiting for Redis ({i+1}/{max_retries})...")
        time.sleep(1)
print("Timeout waiting for Redis!")
sys.exit(1)
EOF
}

case "${SERVICE_TYPE}" in
  api)
    wait_for_redis
    echo "Starting FastAPI Gateway on port ${API_PORT:-8000}..."
    exec uvicorn api.main:app --host 0.0.0.0 --port "${API_PORT:-8000}" --workers 2
    ;;

  beat)
    wait_for_redis
    echo "Starting Celery Beat Scheduler..."
    # Remove stale pid file if exists
    rm -f /app/celerybeat.pid /app/celerybeat-schedule*
    exec celery -A core.celery_app beat -l INFO --schedule=/app/data/celerybeat-schedule
    ;;

  flower)
    wait_for_redis
    echo "Starting Celery Flower Monitoring on port 5555..."
    exec celery -A core.celery_app flower --port=5555 --basic_auth="${FLOWER_BASIC_AUTH:-}"
    ;;

  worker)
    wait_for_redis
    DISPLAY_NUM="${DISPLAY:-:99}"
    RES="${XVFB_RESOLUTION:-1920x1080x24}"
    VNC_P="${VNC_PORT:-5900}"
    NOVNC_P="${NOVNC_PORT:-6080}"
    WORKER_NAME="${CELERY_WORKER_NAME:-worker_$(hostname)}"

    echo "Cleaning old X locks..."
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

    echo "Starting Xvfb on display ${DISPLAY_NUM} (${RES})..."
    Xvfb "${DISPLAY_NUM}" -screen 0 "${RES}" -ac +extension GLX +render -noreset &
    sleep 1

    echo "Starting Window Manager (Fluxbox)..."
    fluxbox &
    sleep 0.5

    echo "Starting x11vnc on port ${VNC_P}..."
    x11vnc -display "${DISPLAY_NUM}" -forever -shared -nopw -rfbport "${VNC_P}" -bg

    echo "Starting noVNC / Websockify on port ${NOVNC_P} (bridged to VNC ${VNC_P})..."
    websockify --web /usr/share/novnc "${NOVNC_P}" "localhost:${VNC_P}" &
    sleep 1

    echo "Starting Celery Worker [${WORKER_NAME}] with concurrency ${CELERY_CONCURRENCY:-2}..."
    echo "Consuming queues: scraping, flows, default"
    echo ">> Stream browser live at: http://<host-ip>:${HOST_NOVNC_PORT:-6081}/vnc.html <<"

    exec celery -A core.celery_app worker \
      -l INFO \
      -n "${WORKER_NAME}@%h" \
      -Q scraping,flows,default \
      -c "${CELERY_CONCURRENCY:-2}"
    ;;

  *)
    if [ "$#" -gt 0 ]; then
      exec "$@"
    else
      echo "No SERVICE_TYPE defined and no command passed. Running bash shell."
      exec /bin/bash
    fi
    ;;
esac
