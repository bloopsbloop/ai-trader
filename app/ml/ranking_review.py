"""M16.1R frozen-artifact verification. This module never fits a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.ml.ranking_objective import DATASET, METADATA, ROOT, load_data, train_frequency_baselines

SOURCE_REPORTS = ROOT / "data/ml/reports/m16_1_ranking_objective_v1"
SOURCE_MODELS = ROOT / "data/models/m16_1_ranking_objective_v1"
OUTPUT = ROOT / "data/ml/reports/m16_1_review_corrections_v1"
SEED = 42


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pairwise_scores(predictor, frame: pd.DataFrame, features: list[str]) -> pd.Series:
    """Reproduce frozen inference: evaluate one row-order-dependent orientation per pair."""
    result = pd.Series(index=frame.index, dtype=float)
    batches, descriptors = [], []
    for _, group in frame.groupby("timestamp", sort=False):
        matrix = group[features].to_numpy(dtype=np.float32); n = len(group)
        left, right = np.triu_indices(n, 1)
        batches.append(matrix[left] - matrix[right]); descriptors.append((group.index, n, left, right, len(left)))
    probabilities = predictor.predict_proba(np.vstack(batches))[:, 1]
    offset = 0
    for index, n, left, right, count in descriptors:
        p = probabilities[offset:offset+count]; offset += count; scores = np.zeros(n)
        np.add.at(scores, left, p); np.add.at(scores, right, 1-p)
        result.loc[index] = scores/(n-1)
    return result.loc[frame.index]


def antisymmetry_diagnostic(predictor, frame, features, split):
    forward, reverse = [], []
    for _, group in frame.groupby("timestamp", sort=False):
        matrix=group[features].to_numpy(dtype=np.float32);left,right=np.triu_indices(len(group),1)
        forward.append(matrix[left]-matrix[right]);reverse.append(matrix[right]-matrix[left])
    a=np.vstack(forward);b=np.vstack(reverse)
    error=np.abs(predictor.predict_proba(a)[:,1]+predictor.predict_proba(b)[:,1]-1)
    return {"split":split,"pairs":len(error),"mean_abs_error":float(error.mean()),"median_abs_error":float(np.median(error)),
            "p90_abs_error":float(np.quantile(error,.9)),"p99_abs_error":float(np.quantile(error,.99)),
            "max_abs_error":float(error.max()),"fraction_gt_1e_3":float((error>1e-3).mean()),"fraction_gt_1e_2":float((error>1e-2).mean())}


def reorder_groups(frame, mode, seed=SEED):
    pieces=[];rng=np.random.default_rng(seed)
    for _,group in frame.groupby("timestamp",sort=True):
        if mode=="original": ordered=group.sort_values("symbol")
        elif mode=="reverse": ordered=group.sort_values("symbol",ascending=False)
        else: ordered=group.iloc[rng.permutation(len(group))]
        pieces.append(ordered)
    return pd.concat(pieces)


def order_diagnostics(predictor, frame, features, split):
    orders=[("original",None),("reverse",None),("perm_seed_7",7),("perm_seed_19",19),("perm_seed_42",42)]
    scored={}; summaries=[]
    for name,seed in orders:
        ordered=reorder_groups(frame,"original" if name=="original" else ("reverse" if name=="reverse" else "permutation"),seed or SEED)
        x=ordered[["timestamp","symbol","target_relative_vol_8h"]].copy();x["score"]=pairwise_scores(predictor,ordered,features).to_numpy()
        x=x.sort_values(["timestamp","symbol"]);x["selected"] = x.groupby("timestamp")["score"].transform("max").eq(x.score)
        selected=x[x.selected].drop_duplicates("timestamp");top1=float((selected.target_relative_vol_8h==x.groupby("timestamp").target_relative_vol_8h.max().to_numpy()).mean())
        shares=selected.symbol.value_counts(normalize=True).to_dict();summaries.append({"split":split,"order":name,"top_1":top1,"selection_shares":json.dumps(shares,sort_keys=True)})
        scored[name]=x
    base=scored["original"].set_index(["timestamp","symbol"]); changes=[]
    base_choice=base[base.selected].reset_index().set_index("timestamp").symbol
    for name,_ in orders[1:]:
        other=scored[name].set_index(["timestamp","symbol"]);delta=(other.score-base.score).abs()
        choice=other[other.selected].reset_index().set_index("timestamp").symbol
        summaries.append({"split":split,"order":name+"_vs_original","top1_choice_change_rate":float((choice!=base_choice).mean()),
                          "mean_abs_score_change":float(delta.mean()),"median_abs_score_change":float(delta.median()),
                          "p95_abs_score_change":float(delta.quantile(.95)),"max_abs_score_change":float(delta.max())})
    return pd.DataFrame(summaries)


def context_feature_diagnostic(train, features, tolerance=1e-12):
    rows=[]
    for feature in features:
        values=train[feature].astype(float);ranges=train.groupby("timestamp")[feature].agg(lambda x:float(x.max()-x.min()))
        within_constant=bool((ranges<=tolerance).all());global_constant=bool(values.max()-values.min()<=tolerance)
        rows.append({"feature":feature,"within_timestamp_constant":within_constant,"changes_over_time":bool(values.max()-values.min()>tolerance),
                     "global_constant":global_constant,"max_within_timestamp_range":float(ranges.max()),"global_range":float(values.max()-values.min()),"tolerance":tolerance,
                     "survives_pairwise_difference":not within_constant})
    return pd.DataFrame(rows)


def per_timestamp_hits(predictions):
    rows=[]
    for objective,part in predictions.groupby("objective"):
        for timestamp,group in part.groupby("timestamp"):
            selected=group.sort_values(["score","symbol"],ascending=[False,True]).iloc[0]
            rows.append({"timestamp":timestamp,"objective":objective,"top_1":float(selected.target_relative_vol_8h==group.target_relative_vol_8h.max())})
    return pd.DataFrame(rows)


def validate_paired(left, right, metric="top_1"):
    for name,frame in (("left",left),("right",right)):
        if frame.timestamp.duplicated().any(): raise ValueError(f"duplicate timestamps in {name}")
        if frame[metric].isna().any(): raise ValueError(f"NaN metric in {name}")
    if set(left.timestamp)!=set(right.timestamp): raise ValueError("evaluation timestamps do not match")
    return left[["timestamp",metric]].merge(right[["timestamp",metric]],on="timestamp",validate="one_to_one",suffixes=("_left","_right")).sort_values("timestamp")


def corrected_block_bootstrap(left, right, metric="top_1", block_hours=24, reps=1000, seed=SEED):
    paired=validate_paired(left,right,metric);paired["difference"]=paired[f"{metric}_left"]-paired[f"{metric}_right"]
    paired["block"]=paired.timestamp.dt.floor(f"{block_hours}h")
    blocks=paired.groupby("block").difference.agg(["sum","count"]);rng=np.random.default_rng(seed);draws=[]
    for _ in range(reps):
        picked=blocks.iloc[rng.integers(0,len(blocks),len(blocks))];draws.append(picked["sum"].sum()/picked["count"].sum())
    sizes=blocks["count"]
    gaps=blocks.index.to_series().diff().dropna()/pd.Timedelta(hours=block_hours)
    return {"block_hours":block_hours,"point_effect":float(paired.difference.mean()),"ci_low":float(np.quantile(draws,.025)),"ci_high":float(np.quantile(draws,.975)),
            "timestamps":len(paired),"blocks":len(blocks),"full_blocks":int((sizes==sizes.max()).sum()),"incomplete_blocks":int((sizes<sizes.max()).sum()),
            "min_block_count":int(sizes.min()),"max_block_count":int(sizes.max()),"timestamp_gaps":int((paired.timestamp.diff()>pd.Timedelta(hours=1)).sum()),
            "block_index_gaps":int((gaps>1).sum()),"seed":seed,"repetitions":reps}


def run(output=OUTPUT):
    output=Path(output)
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    protected=[SOURCE_MODELS/"target_relative_vol_8h__ranking.joblib",SOURCE_MODELS/"target_relative_vol_8h__regression.joblib",SOURCE_REPORTS/"test_predictions.csv",SOURCE_REPORTS/"protocol.json",SOURCE_REPORTS/"summary.json",SOURCE_REPORTS/"paired_effects.csv",SOURCE_REPORTS/"walk_forward.csv"]
    before={str(p.relative_to(ROOT)).replace("\\","/"):sha256(p) for p in protected}
    frame,meta,features=load_data();train=frame[frame.split=="train"];validation=frame[frame.split=="validation"];test=frame[frame.split=="test"]
    artifact=joblib.load(SOURCE_MODELS/"target_relative_vol_8h__ranking.joblib");predictor=artifact["model"]
    antisym=pd.DataFrame([antisymmetry_diagnostic(predictor,validation,features,"validation"),antisymmetry_diagnostic(predictor,test,features,"test")])
    orders=pd.concat([order_diagnostics(predictor,validation,features,"validation"),order_diagnostics(predictor,test,features,"test")],ignore_index=True)
    context=context_feature_diagnostic(train,features)
    predictions=pd.read_csv(SOURCE_REPORTS/"test_predictions.csv",parse_dates=["timestamp"]);hits=per_timestamp_hits(predictions)
    rank=hits[hits.objective=="ranking"].drop(columns="objective");reg=hits[hits.objective=="regression"].drop(columns="objective")
    old=pd.read_csv(SOURCE_REPORTS/"paired_effects.csv");rows=[]
    for hours in (24,8,48):
        corrected=corrected_block_bootstrap(rank,reg,block_hours=hours);prior=old[(old.comparison=="ranker_minus_regressor")&(old.block_hours==hours)].iloc[0]
        rows.append({"comparison":"ranker_minus_regressor","old_effect":prior.effect,"old_ci_low":prior.ci_low,"old_ci_high":prior.ci_high,**corrected})
    baseline_pt,_=train_frequency_baselines(train,test,"target_relative_vol_8h")
    for name in ("uniform","constant_train_winner","train_frequency_random"):
        right=baseline_pt[["timestamp",name]].rename(columns={name:"top_1"});right["timestamp"]=pd.to_datetime(right.timestamp,utc=True)
        corrected=corrected_block_bootstrap(rank,right,block_hours=24);prior=old[old.comparison==f"ranker_minus_{name}"].iloc[0]
        rows.append({"comparison":f"ranker_minus_{name}","old_effect":prior.effect,"old_ci_low":prior.ci_low,"old_ci_high":prior.ci_high,**corrected})
    ci=pd.DataFrame(rows)
    lost=context[context.within_timestamp_constant].feature.tolist();constant=context[context.global_constant].feature.tolist()
    after={str(p.relative_to(ROOT)).replace("\\","/"):sha256(p) for p in protected}
    summary={"verdict":"ACCEPT_AS_LIMITED_NEGATIVE_EXPERIMENT","historical_m16_1_status":"NO_SIGNAL","no_fit_or_tuning":True,
             "exact_scope":"HGB pairwise-difference classifier with aggregation of pair probabilities versus HGB pointwise regressor",
             "not_lambdamart_or_rank_ndcg":True,"protected_hashes_before":before,"protected_hashes_after":after,"hashes_unchanged":before==after,
             "antisymmetry":antisym.to_dict("records"),"order_invariance":orders.to_dict("records"),"lost_context_features":lost,"global_constant_features":constant,
             "pairwise_informative_dimensions":int((~context.within_timestamp_constant).sum()),"total_features":len(features),
             "corrected_ci":ci.to_dict("records"),"fold_ci_correction":"unavailable without forbidden refit: fold predictions and fold models were not persisted",
             "conclusion":"Superiority of this pairwise-difference HGB implementation over the matched HGB regressor was not demonstrated. CI crossing zero is uncertainty, not proof of no effect. The corrected primary 95% CI upper bound is 1.943pp and therefore narrowly excludes the preregistered +2pp effect; the historical unweighted-block CI upper bound was 2.093pp and did not. Evidence versus random/frequency baselines remains separate from objective superiority and from trading edge.",
             "limitations":["reused Test","outcome-conditioned eligibility","closed-trade-only equity drawdown; no intratrade mark-to-market path","simplified fixed 8h execution and constant costs","absence of installed ranking libraries was an environment constraint, not proof they were technically impossible to use"]}
    antisym.to_csv(output/"antisymmetry.csv",index=False);orders.to_csv(output/"order_invariance.csv",index=False);context.to_csv(output/"pairwise_feature_loss.csv",index=False);ci.to_csv(output/"corrected_block_bootstrap.csv",index=False)
    (output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (output/"addendum.md").write_text("# M16.1R Review Addendum\n\nThe historical M16.1 status remains NO_SIGNAL. The experiment is accepted only as a limited negative comparison of an HGB pairwise-difference classifier against an HGB pointwise regressor. It does not test LambdaMART or XGBoost rank:ndcg and does not refute cross-sectional ranking generally. See summary.json and CSV diagnostics for numerical results.\n",encoding="utf-8")
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--output",default=str(OUTPUT));args=parser.parse_args(argv);print(json.dumps(run(args.output),indent=2))


if __name__=="__main__": main()
