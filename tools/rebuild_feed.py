from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from onwatch.io import read_json
from onwatch.publish import rebuild_feed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    result = rebuild_feed(root, read_json(root / "state" / "status.json"))
    print(f"Rebuilt feed with {result['index']['count']} event(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
