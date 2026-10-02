#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/venv/bin/python"

restore_oracles() {
  docker start oracle-1 oracle-2 oracle-3 >/dev/null 2>&1 || true
}
trap restore_oracles EXIT

echo "[1/2] Deteniendo oracle-3: debe mantenerse quórum 2-de-3"
docker stop oracle-3 >/dev/null
"$PYTHON" "$ROOT_DIR/scripts/test_fault_tolerance.py" --expected-signatures 2
docker start oracle-3 >/dev/null

echo "[2/2] Deteniendo oracle-2 y oracle-3: no debe existir quórum"
docker stop oracle-2 oracle-3 >/dev/null
"$PYTHON" "$ROOT_DIR/scripts/test_fault_tolerance.py" --expected-signatures 1

echo "✅ Prueba de tolerancia a fallos completada"
