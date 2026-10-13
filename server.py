"""Convenient local entry point, matching the companion ERA5 viewer."""
import sys
from balloon_predictor.cli import main

if __name__ == '__main__':
    raise SystemExit(main(['serve', *sys.argv[1:]]))
