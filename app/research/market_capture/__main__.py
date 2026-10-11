from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .public_adapter import PublicAdapterConfig, preflight, run_live_capture
from .replay import replay_run

_CONFIRM = "PUBLIC_MARKET_CAPTURE_AUTHORIZED"


def _adapter_config(args: argparse.Namespace) -> PublicAdapterConfig:
    return PublicAdapterConfig(max_elapsed_ns=int(args.duration_seconds * 1_000_000_000))


def main() -> int:
    parser=argparse.ArgumentParser(description="M20.3 public-market capture tools; no connection occurs without explicit capture command and confirmation")
    sub=parser.add_subparsers(dest="command",required=True)
    replay=sub.add_parser("replay",help="offline replay of a saved run")
    replay.add_argument("run_dir",type=Path)
    for name,help_text in (("preflight","offline filesystem/resource checks only"),("capture","explicit future public capture; opens network")):
        command=sub.add_parser(name,help=help_text)
        command.add_argument("--output-root",type=Path,required=True)
        command.add_argument("--run-id",required=True)
        command.add_argument("--duration-seconds",type=float,default=1800.0)
        if name=="capture":
            command.add_argument("--confirm",default="",help=f"must equal {_CONFIRM}")
            command.add_argument("--dry-run",action="store_true",help="perform preflight and exit without network")
    args=parser.parse_args()
    if args.command=="replay":
        print(json.dumps(replay_run(args.run_dir).as_dict(),indent=2,sort_keys=True)); return 0
    config=_adapter_config(args); result=preflight(config,args.output_root,args.run_id)
    if args.command=="preflight" or args.dry_run:
        print(json.dumps(result,indent=2,sort_keys=True)); return 0
    if args.confirm!=_CONFIRM:
        parser.error(f"capture requires --confirm {_CONFIRM}")
    report=asyncio.run(run_live_capture(config,args.output_root,args.run_id))
    print(json.dumps(report.as_dict(),indent=2,sort_keys=True)); return 0


if __name__=="__main__": raise SystemExit(main())