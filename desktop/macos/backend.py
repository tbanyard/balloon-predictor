"""Private Python process started by the macOS desktop app."""
import argparse
import json
import logging
from pathlib import Path
import platform
import sys

# The private interpreter is started with -I, so only this bundled source
# directory is added to its normal standard library and site-packages.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop.launcher import PORT, serve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    with serve(args.data_dir, 0 if args.smoke_test else PORT) as (url, store):
        info = {"url": url, "arch": platform.machine(), "python": platform.python_version()}
        if args.smoke_test:
            from desktop.smoke import check_runtime

            info["download"] = check_runtime(url, store)
        # The readiness signal belongs to this process, even if some other
        # app is already using the desktop port.
        temporary = args.ready_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(info), encoding="utf-8")
        temporary.replace(args.ready_file)
        # EOF arrives on quit, including when the app is force-quit.
        sys.stdin.buffer.read()


if __name__ == "__main__":
    main()
