"""Private engine process used by the macOS and Linux desktop apps."""
import argparse
import logging
from pathlib import Path
import platform
import sys

# The private runtime is started with -I.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop.connection import Chooser, write_json
from desktop.launcher import serve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    info = {"arch": platform.machine(), "python": platform.python_version()}
    if args.smoke_test:
        from desktop.smoke import check_runtime

        with serve(args.data_dir, 0) as (url, store):
            info["download"] = check_runtime(url, store)
    with Chooser(args.data_dir, target_file=args.ready_file.with_name("target.json"),
                 **({"port": 0} if args.smoke_test else {})) as chooser:
        write_json(args.ready_file, {**info, "url": chooser.url})
        # The native window's pipe closes when the app quits.
        sys.stdin.buffer.read()


if __name__ == "__main__":
    main()
