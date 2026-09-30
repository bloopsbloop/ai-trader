"""Fill public candle gaps and build the versioned cross-asset-v2 dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from app.ml.cross_asset_dataset import (DEFAULT_HISTORY, CrossAssetConfig,
                                        CrossAssetDatasetBuilder, export_artifacts)
from app.ml.history_cache import fill_universe

TARGET_SYMBOLS=("BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","LINKUSDT","DOGEUSDT")
DEFAULT_OUTPUT=Path(__file__).resolve().parents[2]/"data"/"ml"/"cross_asset_v2"


def build_v2(history_dir=DEFAULT_HISTORY,output_dir=DEFAULT_OUTPUT,fill=True,fetcher=None):
    started=perf_counter(); downloads=[]
    if fill: downloads=fill_universe(history_dir,TARGET_SYMBOLS,fetcher=fetcher)
    config=CrossAssetConfig(history_dir=str(history_dir),output_dir=str(output_dir),dataset_version="cross-asset-v2",
                            candidate_symbols=TARGET_SYMBOLS)
    frame,features,metadata,coverage=CrossAssetDatasetBuilder(config).build()
    metadata["build_runtime_seconds"]=round(perf_counter()-started,3); metadata["history_fill"]=downloads
    point=frame.groupby("timestamp").agg(eligible_symbols=("symbol",lambda values:",".join(sorted(values))),universe_size=("symbol","size")).reset_index()
    best=frame[frame["is_top_1"]==1]["symbol"].value_counts().sort_index()
    no_trade=frame.groupby("timestamp")["no_trade_preferred"].first()
    metadata["v2_distribution"]={"average_universe_size":float(point.universe_size.mean()),"minimum_universe_size":int(point.universe_size.min()),
        "maximum_universe_size":int(point.universe_size.max()),"universe_size_distribution":{str(k):int(v) for k,v in point.universe_size.value_counts().sort_index().items()},
        "best_symbol_distribution":{str(k):int(v) for k,v in best.items()},"positive_after_costs_rate":float(frame.is_positive_after_costs.mean()),
        "no_trade_timestamps":int(no_trade.sum()),"no_trade_frequency":float(no_trade.mean())}
    paths=export_artifacts(frame,metadata,coverage,output_dir)
    return frame,metadata,coverage,paths


def main(argv=None):
    parser=argparse.ArgumentParser(description="Fill public history and build cross-asset-v2")
    parser.add_argument("--history-dir",default=str(DEFAULT_HISTORY));parser.add_argument("--output",default=str(DEFAULT_OUTPUT));parser.add_argument("--no-fill",action="store_true")
    args=parser.parse_args(argv); frame,metadata,coverage,paths=build_v2(args.history_dir,args.output,not args.no_fill)
    print(json.dumps({"dataset_version":"cross-asset-v2","symbols":metadata["symbols"],"rows":len(frame),"timestamps":metadata["timestamps"],
                      "distribution":metadata["v2_distribution"],"periods":metadata["periods"],"runtime_seconds":metadata["build_runtime_seconds"],"files":paths},indent=2))


if __name__=="__main__":main()
