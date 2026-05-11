#!/usr/bin/env bash
# Container entrypoint:
#   1. (optional) seed MongoDB if SEED_ON_STARTUP=true
#   2. launch FastAPI (uvicorn)
set -euo pipefail

echo "[start] LOG_DIR=${LOG_DIR}  SERVICE=${SERVICE_NAME}"
mkdir -p "${LOG_DIR}"

echo "[start] initialising mongo replica set (idempotent) ..."
python -m init_replset

if [[ "${SEED_ON_STARTUP:-true}" == "true" ]]; then
  echo "[start] running seed_data.py ..."
  python -m seed_data || {
    echo "[start] seeding failed — continuing to start API anyway"
  }
else
  echo "[start] SEED_ON_STARTUP=false, skipping seed."
fi

echo "[start] launching uvicorn on 0.0.0.0:8000 ..."
exec uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log
