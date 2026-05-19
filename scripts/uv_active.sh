#!/usr/bin/env bash
set -euo pipefail

project_slug="$(basename "$PWD" | tr '[:upper:] _' '[:lower:]-' | tr -s '-')"
runtime_venv="${HOME}/.wood/runtime/venvs/${project_slug}"

if [[ ! -d "${runtime_venv}" ]]; then
  cat >&2 <<EOF
Error: expected external runtime venv not found:
  ${runtime_venv}

Create it first:
  mkdir -p "\$HOME/.wood/runtime/venvs"
  python3 -m venv "${runtime_venv}"
EOF
  exit 2
fi

if [[ $# -eq 0 ]]; then
  echo "Usage: bash scripts/uv_active.sh <uv args...>" >&2
  echo "Example: bash scripts/uv_active.sh run pytest" >&2
  exit 2
fi

export VIRTUAL_ENV="${runtime_venv}"
export UV_PROJECT_ENVIRONMENT="${runtime_venv}"
export PATH="${runtime_venv}/bin:${PATH}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

subcommand="$1"
shift

case "${subcommand}" in
  run|sync)
    exec uv "${subcommand}" --active "$@"
    ;;
  *)
    echo "Error: unsupported subcommand '${subcommand}'." >&2
    echo "Use 'run' or 'sync' with this wrapper." >&2
    exit 2
    ;;
esac
