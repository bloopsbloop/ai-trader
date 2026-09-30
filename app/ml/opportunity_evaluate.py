"""Evaluate Opportunity Ranker v1 on the untouched M13.1 Test split."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.cross_asset_dataset import PROJECT_ROOT
from app.ml.opportunity import (OpportunityRanker, clean, decision_distribution, feature_sets, grouped_walk_forward,
                                per_symbol_results, ranked_predictions, ranking_metrics,
                                regression_metrics, select_threshold,
                                simulate, symbol_features)
from app.ml.opportunity_train import DEFAULT_DATASET, DEFAULT_METADATA, DEFAULT_REGISTRY, load_data

DEFAULT_REPORTS = PROJECT_ROOT / "data" / "ml" / "reports" / "opportunity_ranker"


def _csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def evaluate(registry=DEFAULT_REGISTRY, dataset=DEFAULT_DATASET, dataset_metadata_path=DEFAULT_METADATA,
             reports=DEFAULT_REPORTS):
    registry, reports = Path(registry), Path(reports); reports.mkdir(parents=True, exist_ok=True)
    model_meta = json.loads((registry / "metadata.json").read_text(encoding="utf-8"))
    frame, dataset_meta = load_data(dataset, dataset_metadata_path)
    model = OpportunityRanker.load(registry / "model.joblib")
    view = symbol_features(frame) if model_meta["symbol_identity"] else frame
    split_metrics={}
    for split in ("train","validation","test"):
        rows=view[view["split"]==split]; predicted=model.predict(rows)
        split_metrics[split]={"regression":regression_metrics(rows["future_net_return_4h"],predicted),
                              "ranking":ranking_metrics(ranked_predictions(rows,predicted))}
    test=view[view["split"]=="test"].copy(); ranked=ranked_predictions(test,model.predict(test)); threshold=float(model_meta["decision_threshold"])
    trading, ledger=simulate(ranked,threshold); cash={"net_return":0.,"pnl":0.,"trades":0,"win_rate":0.,"profit_factor":0.,"expectancy":0.,"max_drawdown":0.,"fees":0.,"turnover":0.,"time_in_market":0.}
    baselines={"cash_no_trade":cash}
    for name,selector in (("always_btc","btc"),("simple_momentum_rank","momentum"),("oracle_upper_bound_impossible","oracle")):
        baselines[name],_=simulate(ranked,-np.inf,selector)

    ablations=[]; sets=feature_sets(dataset_meta); train=frame[frame["split"]=="train"].copy(); validation=frame[frame["split"]=="validation"].copy()
    for name,features in sets.items():
        ablation=OpportunityRanker(features,model_meta["parameters"]).fit(train)
        val_ranked=ranked_predictions(validation,ablation.predict(validation)); selected,_=select_threshold(val_ranked,100)
        test_ranked=ranked_predictions(test,ablation.predict(test)); test_trade,_=simulate(test_ranked,selected["threshold"])
        ablations.append({"feature_set":name,"feature_count":len(features),"threshold":selected["threshold"],
                          **ranking_metrics(test_ranked),**{f"trading_{k}":v for k,v in test_trade.items() if isinstance(v,(int,float))}})

    walk=grouped_walk_forward(frame,sets["full"],model_meta["parameters"])
    distribution=decision_distribution(ranked,threshold); symbols=per_symbol_results(ledger)
    winners=ranked[ranked["predicted_rank"]==1].copy(); errors=[]
    for row in winners.itertuples(index=False):
        reasons=[]
        chosen=row.predicted_best_return>=threshold
        if row.future_return_rank!=1: reasons.append("wrong_winner")
        if chosen and row.future_net_return_4h<0: reasons.append("selected_asset_lost")
        if not chosen and row.best_available_net_return>=.01: reasons.append("no_trade_missed_strong_opportunity")
        group=ranked[ranked["timestamp"]==row.timestamp]
        if chosen and (group["future_net_return_4h"]<0).all(): reasons.append("traded_all_assets_negative")
        if reasons: errors.append({"timestamp":row.timestamp,"selected":row.symbol if chosen else "NO_TRADE",
                                   "predicted_best_return":row.predicted_best_return,"actual_return":row.future_net_return_4h,
                                   "actual_rank":row.future_return_rank,"actual_best":group.loc[group.future_net_return_4h.idxmax(),"symbol"],
                                   "reasons":";".join(reasons)})
    comparison={"beat_cash":trading["net_return"]>0,"beat_momentum":trading["net_return"]>baselines["simple_momentum_rank"]["net_return"],
                "cross_asset_improved_top1":next(x for x in ablations if x["feature_set"]=="market_cross_asset")["top_1_accuracy"]>next(x for x in ablations if x["feature_set"]=="market_only")["top_1_accuracy"],
                "walk_forward_positive_folds":sum(x["net_return"]>0 for x in walk),"walk_forward_folds":len(walk)}
    selected_decisions=distribution[distribution["selection"]!="NO_TRADE"]
    selected_total=int(selected_decisions["count"].sum())
    collapse={"flagged":bool(selected_total and selected_decisions["count"].max()/selected_total>=.80),
              "threshold":.80,"largest_asset_share_among_trade_decisions":float(selected_decisions["count"].max()/selected_total) if selected_total else 0.}
    summary={"model_version":"opportunity_ranker_v1","research_only":True,"not_for_batch3_control":True,
             "threshold":threshold,"split_metrics":split_metrics,"test_trading":trading,"baselines":baselines,
             "ablation":ablations,"selection_distribution":distribution.to_dict("records"),"selection_collapse":collapse,"symbol_results":symbols.to_dict("records"),
             "walk_forward":walk,"error_counts":pd.Series([reason for row in errors for reason in row["reasons"].split(";")]).value_counts().to_dict(),
             "comparison":comparison,"limitations":["Only BTC/ETH/SOL are eligible; top-3 is not informative.",
             "Historical Test was previously viewed by project research and is not a pristine project-wide holdout.",
             "Oracle is an impossible upper bound and never an implementable baseline.",
             "ML_V1_MARKET omitted because its independent binary target and original execution rule are not a fair ranker comparison."]}
    ranked.to_csv(reports/"test_rankings.csv",index=False); distribution.to_csv(reports/"selection_distribution.csv",index=False); symbols.to_csv(reports/"symbol_results.csv",index=False)
    _csv(reports/"walk_forward.csv",walk); _csv(reports/"ablation.csv",ablations); _csv(reports/"errors.csv",errors)
    (reports/"summary.json").write_text(json.dumps(clean(summary),indent=2,default=str),encoding="utf-8")
    lines=["# Opportunity Ranker v1", "", "Research-only; not connected to Batch 3.", "",
           f"- Test top-1 accuracy: {split_metrics['test']['ranking']['top_1_accuracy']:.4f}",
           f"- Test average Spearman: {split_metrics['test']['ranking']['average_spearman']:.4f}",
           f"- Threshold selected on Validation: {threshold:.8f}", f"- Test net return: {trading['net_return']:.4%}",
           f"- Trades: {trading['trades']}",f"- Max drawdown: {trading['max_drawdown']:.4%}","","## Comparison", "",
           f"- Beat cash: {comparison['beat_cash']}",f"- Beat momentum: {comparison['beat_momentum']}",
           f"- Cross-asset features improved top-1: {comparison['cross_asset_improved_top1']}",
           f"- Positive walk-forward folds: {comparison['walk_forward_positive_folds']}/{comparison['walk_forward_folds']}"]
    (reports/"summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    model_meta["test_evaluated_during_training"]=False; model_meta["evaluation_summary"]=clean(summary)
    (registry/"metadata.json").write_text(json.dumps(clean(model_meta),indent=2),encoding="utf-8")
    return clean(summary)


def main(argv=None):
    parser=argparse.ArgumentParser(description="Evaluate research-only Opportunity Ranker v1")
    parser.add_argument("--registry",default=str(DEFAULT_REGISTRY)); parser.add_argument("--dataset",default=str(DEFAULT_DATASET)); parser.add_argument("--dataset-metadata",default=str(DEFAULT_METADATA)); parser.add_argument("--reports",default=str(DEFAULT_REPORTS))
    args=parser.parse_args(argv); result=evaluate(args.registry,args.dataset,args.dataset_metadata,args.reports)
    print(json.dumps({"test":result["split_metrics"]["test"],"trading":result["test_trading"],"comparison":result["comparison"],"reports":str(Path(args.reports).resolve())},indent=2))


if __name__ == "__main__": main()
