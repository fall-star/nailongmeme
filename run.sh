#!/usr/bin/env bash
#
# Convenience wrapper for the uv-managed project environment.
#
#   ./run.sh                 # show CLI help
#   ./run.sh verify
#   ./run.sh search "开心"
#   ./run.sh crawl --source nailong
#
# The project .venv is managed by uv (pyproject.toml + uv.lock). If it is
# missing or lacks Pillow, this script runs `uv sync` with a project-local
# cache. A valid .venv is always preferred so normal commands are fast.
# MEME_PYTHON=/path/to/python overrides the managed environment if needed.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# No subcommand: show help instead of an argparse "required command" error.
if [[ $# -eq 0 ]]; then
  set -- --help
fi

has_pillow() {
  "$1" -c 'import PIL' >/dev/null 2>&1
}

exec_with_python() {
  local python="$1"
  shift
  if [[ "${1:-}" == "convert" ]]; then
    shift
    exec "$python" "$ROOT/convert_formats.py" "$@"
  fi
  if [[ "${1:-}" == "thumbs" ]]; then
    shift
    exec "$python" "$ROOT/tools/build_thumbnails.py" "$@"
  fi
  if [[ "${1:-}" == "admin" ]]; then
    shift
    exec "$python" -m uvicorn admin.server:app --host 127.0.0.1 --port 8001 "$@"
  fi
  exec "$python" -m meme_pipeline "$@"
}

if [[ -n "${MEME_PYTHON:-}" ]]; then
  if ! command -v "$MEME_PYTHON" >/dev/null 2>&1; then
    echo "error: Python interpreter not found: $MEME_PYTHON" >&2
    exit 1
  fi
  if ! has_pillow "$MEME_PYTHON"; then
    echo "error: Pillow is not installed for $MEME_PYTHON" >&2
    echo "  use the managed environment instead: ./run.sh verify" >&2
    exit 1
  fi
  exec_with_python "$MEME_PYTHON" "$@"
fi

# 1. Preferred: the uv-managed virtual environment.
if [[ -x "$ROOT/.venv/bin/python" ]] && has_pillow "$ROOT/.venv/bin/python"; then
  exec_with_python "$ROOT/.venv/bin/python" "$@"
fi

# 2. Bootstrap/repair the uv environment if uv is available.
UV_BIN="$(command -v uv 2>/dev/null || true)"
if [[ -z "$UV_BIN" ]]; then
  for candidate in "$HOME/.local/bin/uv" "/opt/homebrew/bin/uv" "/usr/local/bin/uv"; do
    if [[ -x "$candidate" ]]; then
      UV_BIN="$candidate"
      break
    fi
  done
fi

if [[ -n "$UV_BIN" ]]; then
  echo "uv: syncing project environment..." >&2
  if (
    cd "$ROOT"
    UV_CACHE_DIR="$ROOT/.uv-cache" "$UV_BIN" sync --quiet --python 3.12
  ); then
    if [[ -x "$ROOT/.venv/bin/python" ]] && has_pillow "$ROOT/.venv/bin/python"; then
      exec_with_python "$ROOT/.venv/bin/python" "$@"
    fi
  fi

  # Last uv fallback: let uv create/sync on demand.
  if [[ "${1:-}" == "convert" ]]; then
    exec env UV_CACHE_DIR="$ROOT/.uv-cache" \
      "$UV_BIN" run --project "$ROOT" --python 3.12 \
      python "$ROOT/convert_formats.py" "${@:2}"
  fi
  if [[ "${1:-}" == "thumbs" ]]; then
    exec env UV_CACHE_DIR="$ROOT/.uv-cache" \
      "$UV_BIN" run --project "$ROOT" --python 3.12 \
      python "$ROOT/tools/build_thumbnails.py" "${@:2}"
  fi
  exec env UV_CACHE_DIR="$ROOT/.uv-cache" \
    "$UV_BIN" run --project "$ROOT" --python 3.12 \
    python -m meme_pipeline "$@"
fi

# 3. Compatibility fallback for machines without uv.
candidates=()
[[ -x "$ROOT/.venv/bin/python" ]] && candidates+=("$ROOT/.venv/bin/python")
candidates+=("python3" "python3.14" "python3.13" "python3.12" "python3.11" "python3.10")
if [[ -n "${DSH_HOME:-}" ]]; then
  while IFS= read -r candidate; do
    candidates+=("$candidate")
  done < <(compgen -G "$DSH_HOME/dsh-runtimes/*/dependencies/python/bin/python3" || true)
fi

for candidate in "${candidates[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1 && has_pillow "$candidate"; then
    exec_with_python "$candidate" "$@"
  fi
done

echo "error: no uv environment or Python with Pillow found" >&2
echo "  install uv, then run: uv sync --python 3.12" >&2
echo "  or set: MEME_PYTHON=/path/to/python-with-pillow ./run.sh verify" >&2
exit 1
