from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from typing import Callable, Sequence


def run_measured(argv: Sequence[str], *,
                 utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic_ns: Callable[[], int] = time.monotonic_ns,
                 runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run) -> dict[str, object]:
    if not argv: raise ValueError("child command is required")
    started_utc = utc_now(); started_mono = monotonic_ns()
    result = runner(list(argv), check=False)
    ended_mono = monotonic_ns(); ended_utc = utc_now()
    return {
        "argv": list(argv),
        "start_utc": started_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end_utc": ended_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "elapsed_ns": ended_mono - started_mono,
        "exit_code": int(result.returncode),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Shell-free child-process timing wrapper")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    record = run_measured(command)
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        with args.json_output.open("x", encoding="utf-8", newline="\n") as handle: handle.write(payload)
    else: print(payload, end="")
    return int(record["exit_code"])


if __name__ == "__main__": raise SystemExit(main())
