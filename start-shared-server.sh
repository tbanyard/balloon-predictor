#!/usr/bin/env bash
# One shared Linux service, with personal workspaces verified through SSH.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
shared_root=/data1/tim/BP_Data
port=8011
host=0.0.0.0
old_source=
old_config="$source_dir/config.yml"
fresh=false
while (($#)); do
    case "$1" in
        --port) port=$2; shift ;;
        --host) host=$2; shift ;;
        --shared-root) shared_root=$2; shift ;;
        --source) old_source=$2; shift ;;
        --config) old_config=$2; shift ;;
        --fresh) fresh=true ;;
        -h|--help)
            printf '%s\n' "Usage: bash start-shared-server.sh [--port 8011] [--host 0.0.0.0] [--source OLD_DATA] [--config OLD_CONFIG] [--fresh]"
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
    esac
    shift
done
[[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || { printf 'Invalid server port.\n' >&2; exit 1; }
profile="$shared_root/.server-$port"
mkdir -p -- "$profile"
[[ ! -L "$profile" && -O "$profile" ]] || { printf 'The server profile is not owned by this Unix account.\n' >&2; exit 1; }
chmod 755 -- "$profile"
mkdir -p -- "$profile/tmp" "$profile/pip-cache"
chmod 700 -- "$profile/tmp" "$profile/pip-cache"
export TMPDIR="$profile/tmp" PIP_CACHE_DIR="$profile/pip-cache" PIP_DISABLE_PIP_VERSION_CHECK=1
python=${BP_PYTHON:-python3}
"$python" -c 'import sys; assert sys.version_info >= (3,12), "Activate Python 3.12 or newer, then run this script again."'
"$python" - "$port" <<'PY'
import socket, sys
try:
    connection = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1)
except OSError:
    pass
else:
    connection.close()
    raise SystemExit("Stopped: that server port is in use. Stop the old server before running this launcher.")
PY
if [[ ! -x "$profile/venv/bin/python" ]]; then "$python" -m venv "$profile/venv"; fi
python="$profile/venv/bin/python"
"$python" -m pip install -r "$source_dir/requirements.lock.txt"
cd -- "$source_dir"
args=(--shared-root "$shared_root" --port "$port" --config "$old_config")
[[ -z "$old_source" ]] || args+=(--source "$old_source")
"$fresh" && args+=(--fresh)
"$python" -m balloon_predictor.migrate_storage "${args[@]}"
# Apply the reviewed server profile, rather than a previous shell's overrides.
unset BP_DATA_DIR BP_ERA5_ROOT BP_WRF_ROOT BP_TERRAIN_DIR BP_SHARED_DATA_ROOT BP_CONFIG
printf '\nStarting BalloonPredictor. Stop with Ctrl+C.\nApp log: %s/server.log\n' "$profile"
exec "$python" server.py --config "$profile/config.yml" --host "$host" --port "$port" 2>&1 | tee -a "$profile/server.log"
