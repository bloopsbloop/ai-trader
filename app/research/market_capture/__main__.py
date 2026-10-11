from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .host_space import HostSpaceUnavailableError, WindowsVhdxSpaceMonitor
from .public_adapter import (EXTENDED_CONFIRM, STAGE_A_CONFIRM, PublicAdapterConfig,
                             capture_profile, preflight, run_live_capture)
from .replay import replay_run

def _adapter_config(args: argparse.Namespace) -> PublicAdapterConfig:
    return capture_profile(args.profile, duration_seconds=args.duration_seconds)


def main() -> int:
    parser=argparse.ArgumentParser(description="M20.3 public-market capture tools; no connection occurs without explicit capture command and confirmation")
    sub=parser.add_subparsers(dest="command",required=True)
    replay=sub.add_parser("replay",help="offline replay of a saved run")
    replay.add_argument("run_dir",type=Path)
    for name,help_text in (("preflight","offline filesystem/resource checks only"),("capture","explicit future public capture; opens network")):
        command=sub.add_parser(name,help=help_text)
        command.add_argument("--output-root",type=Path,required=True)
        command.add_argument("--run-id",required=True)
        command.add_argument("--profile",choices=("stage-a","extended"),default="stage-a")
        command.add_argument("--duration-seconds",type=float)
        command.add_argument("--windows-vhdx-path",
            help="required for extended profile physical-host checks")
        if name=="capture":
            command.add_argument("--confirm",default="",help="must match the selected profile")
            command.add_argument("--dry-run",action="store_true",help="perform preflight and exit without network")
    args=parser.parse_args()
    if args.command=="replay":
        print(json.dumps(replay_run(args.run_dir).as_dict(),indent=2,sort_keys=True)); return 0
    config=_adapter_config(args); host_monitor=None
    if config.profile=="extended":
        if not args.windows_vhdx_path:
            parser.error("extended profile requires --windows-vhdx-path")
        try: host_monitor=WindowsVhdxSpaceMonitor(args.windows_vhdx_path)
        except (ValueError,HostSpaceUnavailableError) as exc: parser.error(str(exc))
    result=preflight(config,args.output_root,args.run_id,host_free_space=host_monitor)
    if args.command=="preflight" or args.dry_run:
        print(json.dumps(result,indent=2,sort_keys=True)); return 0
    expected=STAGE_A_CONFIRM if config.profile=="stage-a" else EXTENDED_CONFIRM
    if args.confirm!=expected:
        parser.error(f"capture requires --confirm {expected}")
    report=asyncio.run(run_live_capture(config,args.output_root,args.run_id,
                                        host_free_space=host_monitor))
    print(json.dumps(report.as_dict(),indent=2,sort_keys=True)); return 0


if __name__=="__main__": raise SystemExit(main())
