#!/usr/bin/env bash
# Ejecuta los scripts de extraccion leyendo la configuracion de scripts/.env
#
# Uso:
#   ./sync.sh                  # kiwi + todos los grupos gitlab habilitados (+ tokens si TOKENS_ENABLED)
#   ./sync.sh kiwi
#   ./sync.sh gitlab           # todos los grupos habilitados
#   ./sync.sh gitlab core docs # solo esos grupos
#   ./sync.sh tokens
#   ./sync.sh list             # muestra la configuracion
#   ./sync.sh --dry-run        # muestra los comandos sin ejecutarlos
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv no esta instalado: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

exec uv run --no-project --quiet \
  --with python-dotenv \
  --with tcms-api \
  --with tiktoken \
  python "$SCRIPT_DIR/sync.py" "$@"
