#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "${BP_PYTHON:-python3}" "$here/scripts/release-v1.6.6.py" "$@"
