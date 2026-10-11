from __future__ import annotations

import argparse
import json
from pathlib import Path

from .replay import replay_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline-only M20.2R verified/recovery replay; no network transport exists")
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("replay", help="replay a saved synthetic raw.jsonl namespace")
    replay.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    if args.command == "replay":
        print(json.dumps(replay_run(args.run_dir).as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
