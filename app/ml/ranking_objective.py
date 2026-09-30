"""M16.1 ranking-objective research. Research only; never imported by trading code."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/ml/nextgen_target_v1/dataset.csv.gz"
METADATA = ROOT / "data/ml/nextgen_target_v1/metadata.json"
REPORTS = ROOT / "data/ml/reports/m16_1_ranking_objective_v1"
MODEL_DIR = ROOT / "data/models/m16_1_ranking_objective_v1"
SEED = 42
CONFIGS = {
    "hgb15": dict(max_iter=70, max_leaf_nodes=15, learning_rate=.05, min_samples_leaf=150, l2_regularization=1.),
    "hgb31": dict(max_iter=70, max_leaf_nodes=31, learning_rate=.05, min_samples_leaf=150, l2_regularization=1.),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_data(dataset=DATASET, metadata=METADATA):
    frame = pd.read_csv(dataset, parse_dates=["timestamp", "target_timestamp_8h", "source_open_time"])
    meta = json.loads(Path(metadata).read_text(encoding="utf-8"))
    features = meta["schemas"]["stability_filtered_features"]
    if len(features) != 86 or any(name in meta["schemas"]["target_columns"] for name in features):
        raise ValueError("invalid stability_filtered_86 schema")
    return frame.sort_values(["timestamp", "symbol"]).reset_index(drop=True), meta, features


def ordinal_relevance(values: pd.Series) -> np.ndarray:
    """Linear ordinal relevance 0..n-1; ties receive equal average-rank relevance."""
    ranks = values.rank(method="average", ascending=True).to_numpy(dtype=float) - 1
    return ranks


def make_pairwise(frame: pd.DataFrame, features: list[str], target: str, seed=SEED):
    """One randomly oriented row per strict within-timestamp pair; tied labels are omitted."""
    rng = np.random.default_rng(seed)
    xs, ys = [], []
    for _, group in frame.groupby("timestamp", sort=True):
        matrix = group[features].to_numpy(dtype=np.float32)
        labels = group[target].to_numpy()
        left, right = np.triu_indices(len(group), 1)
        strict = labels[left] != labels[right]
        left, right = left[strict], right[strict]
        if not len(left):
            continue
        flip = rng.integers(0, 2, len(left)).astype(bool)
        a = np.where(flip, right, left)
        b = np.where(flip, left, right)
        xs.append(matrix[a] - matrix[b])
        ys.append((labels[a] > labels[b]).astype(np.uint8))
    return np.vstack(xs), np.concatenate(ys)


class PairwiseRanker:
    def __init__(self, features, target, config_id="hgb15", seed=SEED):
        self.features, self.target, self.config_id, self.seed = list(features), target, config_id, seed
        self.model = HistGradientBoostingClassifier(loss="log_loss", random_state=seed, early_stopping=False,
                                                     **CONFIGS[config_id])

    def fit(self, frame):
        x, y = make_pairwise(frame, self.features, self.target, self.seed)
        self.model.fit(x, y)
        self.pair_rows_ = len(y)
        return self

    def predict(self, frame):
        result = pd.Series(index=frame.index, dtype=float)
        batches = []
        descriptors = []
        for _, group in frame.groupby("timestamp", sort=False):
            matrix = group[self.features].to_numpy(dtype=np.float32)
            n = len(group)
            if n == 1:
                result.loc[group.index] = 0.
                continue
            left, right = np.triu_indices(n, 1)
            batches.append(matrix[left] - matrix[right])
            descriptors.append((group.index, n, left, right, len(left)))
        probabilities = self.model.predict_proba(np.vstack(batches))[:, 1] if batches else np.array([])
        offset = 0
        for index, n, left, right, count in descriptors:
            p = probabilities[offset:offset+count]; offset += count
            scores = np.zeros(n)
            np.add.at(scores, left, p)
            np.add.at(scores, right, 1 - p)
            result.loc[index] = scores / (n - 1)
        return result.loc[frame.index].to_numpy()


class MatchedRegressor:
    def __init__(self, features, target, config_id="hgb15", seed=SEED):
        self.features, self.target, self.config_id, self.seed = list(features), target, config_id, seed
        self.model = HistGradientBoostingRegressor(loss="squared_error", random_state=seed, early_stopping=False,
                                                    **CONFIGS[config_id])

    def fit(self, frame):
        self.model.fit(frame[self.features], frame[self.target])
        return self

    def predict(self, frame):
        return self.model.predict(frame[self.features])


def rank_frame(frame, scores, target):
    x = frame[["timestamp", "target_timestamp_8h", "symbol", "split", target,
               "future_net_return_8h", "momentum_4_1h", "return_rank_24h",
               "behavior_regime", "market_state"]].copy()
    x["score"] = scores
    x["predicted_rank"] = x.groupby("timestamp")["score"].rank(method="first", ascending=False)
    x["actual_rank"] = x.groupby("timestamp")[target].rank(method="min", ascending=False)
    return x


def ranking_metrics(ranked, target):
    rows = []
    for timestamp, group in ranked.groupby("timestamp", sort=True):
        ordered = group.sort_values(["score", "symbol"], ascending=[False, True])
        best = group[target].max()
        winners = set(group.loc[group[target] == best, "symbol"])
        chosen = ordered.symbol.tolist()
        first = min(i + 1 for i, symbol in enumerate(chosen) if symbol in winners)
        relevance = ordinal_relevance(group[target])
        rel = pd.Series(relevance, index=group.index)
        ideal = np.sort(relevance)[::-1]
        predicted = rel.loc[ordered.index].to_numpy()
        def ndcg(k):
            k = min(k, len(group)); discount = 1 / np.log2(np.arange(2, k + 2))
            denominator = float((ideal[:k] * discount).sum())
            return float((predicted[:k] * discount).sum() / denominator) if denominator else 1.
        corr = spearmanr(group.score, group[target]).statistic if len(group) > 1 else np.nan
        rows.append({"timestamp": timestamp, "top_1": float(first <= 1), "top_2": float(first <= 2),
                     "top_3": float(first <= 3), "mrr": 1 / first, "spearman": corr,
                     "ndcg_at_1": ndcg(1), "ndcg_at_3": ndcg(3), "universe": len(group),
                     "winner_count": len(winners), "selected_symbol": chosen[0],
                     "raw_winner_hit": float(ordered.iloc[0].future_net_return_8h == group.future_net_return_8h.max())})
    per_time = pd.DataFrame(rows)
    metrics = {key: float(per_time[key].mean()) for key in
               ["top_1", "top_2", "top_3", "mrr", "spearman", "ndcg_at_1", "ndcg_at_3", "raw_winner_hit"]}
    metrics["groups"] = len(per_time)
    return metrics, per_time


def grouped_boundaries(frame):
    times = np.array(sorted(frame.timestamp.unique()))
    specs = [(.40, .50, .667), (.50, .667, .834), (.667, .834, 1.)]
    for fold, (a, b, c) in enumerate(specs, 1):
        ta, tb = times[int(len(times) * a)], times[int(len(times) * b)]
        tc = times[min(len(times) - 1, int(len(times) * c) - 1)]
        train = frame[(frame.timestamp < ta) & (frame.target_timestamp_8h < ta)]
        validation = frame[(frame.timestamp >= ta) & (frame.timestamp < tb) & (frame.target_timestamp_8h < tb)]
        evaluation = frame[(frame.timestamp >= tb) & (frame.timestamp <= tc)]
        yield fold, train, validation, evaluation


def select_configuration(train, validation, features, target):
    candidates = []
    models = {}
    for objective, cls in (("regression", MatchedRegressor), ("ranking", PairwiseRanker)):
        for config_id in CONFIGS:
            model = cls(features, target, config_id).fit(train)
            metrics, _ = ranking_metrics(rank_frame(validation, model.predict(validation), target), target)
            row = {"objective": objective, "config_id": config_id, **metrics}
            candidates.append(row); models[(objective, config_id)] = model
    selected = {}
    for objective in ("regression", "ranking"):
        eligible = [r for r in candidates if r["objective"] == objective]
        winner = max(eligible, key=lambda r: (r["top_1"], r["ndcg_at_3"], -CONFIGS[r["config_id"]]["max_leaf_nodes"]))
        selected[objective] = (winner, models[(objective, winner["config_id"])])
    return pd.DataFrame(candidates), selected


def train_frequency_baselines(train, evaluation, target):
    winners = train.loc[train.groupby("timestamp")[target].idxmax(), "symbol"].value_counts(normalize=True)
    fallback = winners.index[0]
    rows = []
    rng = np.random.default_rng(SEED)
    for timestamp, group in evaluation.groupby("timestamp", sort=True):
        available = group.symbol.tolist(); best = group[target].max(); win = set(group.loc[group[target] == best, "symbol"])
        constant = fallback if fallback in available else max(available, key=lambda s: winners.get(s, 0.))
        weights = np.array([winners.get(s, 0.) for s in available]); weights = weights / weights.sum() if weights.sum() else np.ones(len(group))/len(group)
        probabilistic = rng.choice(available, p=weights)
        rows.append({"timestamp": timestamp, "uniform": len(win)/len(group), "constant_train_winner": float(constant in win),
                     "train_frequency_random": float(probabilistic in win)})
    return pd.DataFrame(rows), winners.to_dict()


def paired_block_bootstrap(left, right, metric="top_1", block_hours=24, reps=1000, seed=SEED):
    paired = left[["timestamp", metric]].merge(right[["timestamp", metric]], on="timestamp", suffixes=("_left", "_right"))
    paired["block"] = paired.timestamp.dt.floor(f"{block_hours}h")
    block_values = paired.groupby("block").apply(lambda x: x[f"{metric}_left"].mean()-x[f"{metric}_right"].mean(), include_groups=False).to_numpy()
    rng = np.random.default_rng(seed)
    draws = np.array([rng.choice(block_values, len(block_values), replace=True).mean() for _ in range(reps)])
    return {"metric": metric, "block_hours": block_hours, "effect": float(paired[f"{metric}_left"].mean()-paired[f"{metric}_right"].mean()),
            "ci_low": float(np.quantile(draws, .025)), "ci_high": float(np.quantile(draws, .975)),
            "blocks": len(block_values), "groups": len(paired)}


def fixed_schedule(ranked, method, exposure=.10):
    equity = peak = 1000.; available = ranked.timestamp.min(); ledger = []
    for timestamp, group in ranked.groupby("timestamp", sort=True):
        if timestamp < available: continue
        row = group.sort_values(["score", "symbol"], ascending=[False, True]).iloc[0]
        universe_mean = group.future_net_return_8h.mean(); universe_median = group.future_net_return_8h.median()
        before = equity; pnl = before * exposure * row.future_net_return_8h; equity += pnl; peak = max(peak, equity)
        available = row.target_timestamp_8h
        ledger.append({"timestamp": timestamp, "method": method, "symbol": row.symbol, "net_return": row.future_net_return_8h,
                       "notional": before*exposure, "included_round_trip_cost": before*exposure*.0018,
                       "return_minus_universe_mean": row.future_net_return_8h-universe_mean,
                       "return_minus_universe_median": row.future_net_return_8h-universe_median,
                       "pnl": pnl, "equity": equity, "drawdown": equity/peak-1})
    ledger = pd.DataFrame(ledger)
    positives = ledger.loc[ledger.pnl > 0, "pnl"].sum(); negatives = ledger.loc[ledger.pnl <= 0, "pnl"].sum()
    return {"method": method, "trades": len(ledger), "net_return": equity/1000-1, "pnl": equity-1000,
            "mean_selected_return": ledger.net_return.mean(), "median_selected_return": ledger.net_return.median(),
            "mean_return_minus_universe_mean": ledger.return_minus_universe_mean.mean(),
            "mean_return_minus_universe_median": ledger.return_minus_universe_median.mean(),
            "negative_selection_rate": (ledger.net_return < 0).mean(), "profit_factor": positives/abs(negatives) if negatives else np.inf,
            "max_drawdown": abs(ledger.drawdown.min()), "exposure_per_trade": exposure,
            "turnover": float((ledger.notional*2).sum()), "included_costs": float(ledger.included_round_trip_cost.sum())}, ledger


def add_baseline_scores(frame, selector):
    scores = frame[selector].to_numpy() if selector in frame else np.zeros(len(frame))
    return rank_frame(frame, scores, "target_relative_vol_8h")


def importance_rows(model, validation, objective, sample_rows=10000):
    rng=np.random.default_rng(SEED)
    if objective=="ranking":
        x,y=make_pairwise(validation,model.features,model.target)
        take=rng.choice(len(y),min(sample_rows,len(y)),replace=False);x,y=x[take],y[take]
        result=permutation_importance(model.model,x,y,n_repeats=2,random_state=SEED,scoring="roc_auc")
    else:
        part=validation.sample(min(sample_rows,len(validation)),random_state=SEED)
        result=permutation_importance(model.model,part[model.features],part[model.target],n_repeats=2,random_state=SEED,scoring="neg_mean_squared_error")
    return [{"objective":objective,"feature":feature,"importance":float(value)} for feature,value in zip(model.features,result.importances_mean)]


def run(output=REPORTS, model_dir=MODEL_DIR):
    output, model_dir = Path(output), Path(model_dir)
    if not (output/"protocol.json").exists() or not (output/"audit.json").exists():
        raise FileNotFoundError("frozen protocol and audit must exist before training")
    existing = [p for p in output.iterdir() if p.name not in {"protocol.json", "audit.json"}]
    if existing or model_dir.exists(): raise FileExistsError("M16.1 namespace already contains results")
    frame, meta, features = load_data(); train=frame[frame.split=="train"]; val=frame[frame.split=="validation"]; test=frame[frame.split=="test"]
    all_candidates=[]; selected_models={}; final_rows=[]; per_times={}; predictions=[]
    for target in ("target_relative_vol_8h", "path_quality_score"):
        table, selected = select_configuration(train, val, features, target); table["target"] = target; all_candidates.append(table)
        for objective, (selection, model) in selected.items():
            selected_models[(target,objective)] = model
            for split, part in (("validation",val),("test",test)):
                ranked=rank_frame(part,model.predict(part),target); metrics,pt=ranking_metrics(ranked,target)
                final_rows.append({"target":target,"objective":objective,"config_id":selection["config_id"],"split":split,**metrics})
                per_times[(target,objective,split)]=pt
                if target=="target_relative_vol_8h" and split=="test":
                    ranked["objective"]=objective;predictions.append(ranked)
    # Frozen M15.3 is loaded and applied without fitting.
    frozen=joblib.load(ROOT/"data/models/nextgen_relative_8h_v1/model.joblib")
    frozen_scores=frozen["model"].predict(test[frozen["features"]])
    frozen_ranked=rank_frame(test,frozen_scores,"target_relative_vol_8h");fm,fp=ranking_metrics(frozen_ranked,"target_relative_vol_8h")
    final_rows.append({"target":"target_relative_vol_8h","objective":"frozen_m15_3","config_id":"frozen","split":"test",**fm});per_times[("target_relative_vol_8h","frozen_m15_3","test")]=fp
    baseline_pt, winner_frequency=train_frequency_baselines(train,test,"target_relative_vol_8h")
    baseline_rows=[]
    for name in ("uniform","constant_train_winner","train_frequency_random"):
        baseline_rows.append({"baseline":name,"top_1":float(baseline_pt[name].mean()),"groups":len(baseline_pt)})
    rng=np.random.default_rng(SEED); mc=[]
    sizes=test.groupby("timestamp").size().to_numpy(); winner_counts=test.groupby("timestamp").apply(lambda g:(g.target_relative_vol_8h==g.target_relative_vol_8h.max()).sum(),include_groups=False).to_numpy()
    for _ in range(2000): mc.append(np.mean(rng.random(len(sizes)) < winner_counts/sizes))
    baseline_rows.append({"baseline":"uniform_monte_carlo","top_1":float(np.mean(mc)),"ci_low":float(np.quantile(mc,.025)),"ci_high":float(np.quantile(mc,.975)),"groups":len(sizes)})
    # Paired uncertainty on reused Test.
    effects=[];rank_pt=per_times[("target_relative_vol_8h","ranking","test")];reg_pt=per_times[("target_relative_vol_8h","regression","test")]
    for hours in (24,8,48):effects.append({"comparison":"ranker_minus_regressor",**paired_block_bootstrap(rank_pt,reg_pt,block_hours=hours)})
    for name in ("uniform","constant_train_winner","train_frequency_random"):
        right=baseline_pt[["timestamp",name]].rename(columns={name:"top_1"});effects.append({"comparison":f"ranker_minus_{name}",**paired_block_bootstrap(rank_pt,right)})
    # Walk-forward with per-fold Validation-only config selection.
    wf=[]
    for fold,tr,va,ev in grouped_boundaries(frame):
        table,chosen=select_configuration(tr,va,features,"target_relative_vol_8h")
        fold_pts={}
        for objective,(selection,model) in chosen.items():
            ranked=rank_frame(ev,model.predict(ev),"target_relative_vol_8h");m,pt=ranking_metrics(ranked,"target_relative_vol_8h");econ,_=fixed_schedule(ranked,f"fold{fold}_{objective}")
            wf.append({"fold":fold,"objective":objective,"config_id":selection["config_id"],"train_start":tr.timestamp.min(),"train_end":tr.timestamp.max(),"validation_start":va.timestamp.min(),"validation_end":va.timestamp.max(),"evaluation_start":ev.timestamp.min(),"evaluation_end":ev.timestamp.max(),"train_rows":len(tr),"validation_rows":len(va),"evaluation_rows":len(ev),**m,"economic_net_return":econ["net_return"]});fold_pts[objective]=pt
        effect=paired_block_bootstrap(fold_pts["ranking"],fold_pts["regression"])
        wf.append({"fold":fold,"objective":"paired_effect","top_1":effect["effect"],"ci_low":effect["ci_low"],"ci_high":effect["ci_high"]})
    # Economic comparisons use identical fixed schedule and no score gate.
    economics=[];ledgers=[]
    for target,objective in (("target_relative_vol_8h","ranking"),("target_relative_vol_8h","regression")):
        ranked=rank_frame(test,selected_models[(target,objective)].predict(test),target);m,l=fixed_schedule(ranked,objective);economics.append(m);ledgers.append(l)
    m,l=fixed_schedule(frozen_ranked,"frozen_m15_3_no_threshold");economics.append(m);ledgers.append(l)
    for name,column in (("momentum","momentum_4_1h"),("relative_strength","return_rank_24h")):
        ranked=add_baseline_scores(test,column);m,l=fixed_schedule(ranked,name);economics.append(m);ledgers.append(l)
    economics.append({"method":"cash","trades":0,"net_return":0.,"pnl":0.,"max_drawdown":0.})
    # Score-gap buckets fixed from Validation quartiles, diagnostic only.
    gap_rows=[]
    for objective in ("ranking","regression"):
        model=selected_models[("target_relative_vol_8h",objective)]
        rv=rank_frame(val,model.predict(val),"target_relative_vol_8h");rt=rank_frame(test,model.predict(test),"target_relative_vol_8h")
        def gaps(x):
            return x.groupby("timestamp").apply(lambda g: pd.Series({"gap":g.score.nlargest(2).iloc[0]-g.score.nlargest(2).iloc[1],"hit":float(g.loc[g.score.idxmax(),"target_relative_vol_8h"]==g.target_relative_vol_8h.max())}),include_groups=False).reset_index()
        vg,tg=gaps(rv),gaps(rt);edges=np.unique(vg.gap.quantile([0,.25,.5,.75,1]).to_numpy());
        if len(edges)<3: edges=np.array([-np.inf,np.median(vg.gap),np.inf])
        edges[0],edges[-1]=-np.inf,np.inf;tg["bucket"]=pd.cut(tg.gap,edges,include_lowest=True,duplicates="drop")
        for bucket,g in tg.groupby("bucket",observed=True):gap_rows.append({"objective":objective,"bucket":str(bucket),"groups":len(g),"mean_gap":g.gap.mean(),"top_1":g.hit.mean()})
    # Symbol and state diagnostics for primary ranker.
    primary_pred=pd.concat(predictions).query("objective=='ranking'")
    chosen=primary_pred[primary_pred.predicted_rank==1]
    symbol=chosen.groupby("symbol").agg(selections=("symbol","size"),mean_net_return=("future_net_return_8h","mean"),winner_hit=("actual_rank",lambda x:(x==1).mean())).reset_index();symbol["selection_share"]=symbol.selections/symbol.selections.sum()
    states=[]
    for dimension in ("behavior_regime","market_state"):
        for state,g in primary_pred.groupby(dimension):
            m,_=ranking_metrics(g,"target_relative_vol_8h");states.append({"dimension":dimension,"state":state,"rows":len(g),**m})
    output.mkdir(parents=True,exist_ok=True);model_dir.mkdir(parents=True,exist_ok=False)
    pd.concat(all_candidates).to_csv(output/"validation_selection.csv",index=False);pd.DataFrame(final_rows).to_csv(output/"metrics.csv",index=False)
    pd.DataFrame(baseline_rows).to_csv(output/"baselines.csv",index=False);pd.DataFrame(effects).to_csv(output/"paired_effects.csv",index=False)
    pd.DataFrame(wf).to_csv(output/"walk_forward.csv",index=False);pd.DataFrame(economics).to_csv(output/"economic_diagnostics.csv",index=False)
    pd.concat(ledgers).to_csv(output/"economic_ledger.csv",index=False);pd.DataFrame(gap_rows).to_csv(output/"score_gap.csv",index=False)
    symbol.to_csv(output/"symbol_analysis.csv",index=False);pd.DataFrame(states).to_csv(output/"market_state_analysis.csv",index=False)
    pd.concat(predictions).to_csv(output/"test_predictions.csv",index=False)
    importance=[]
    for objective in ("ranking","regression"):
        importance.extend(importance_rows(selected_models[("target_relative_vol_8h",objective)],val,objective))
    pd.DataFrame(importance).sort_values(["objective","importance"],ascending=[True,False]).to_csv(output/"feature_importance.csv",index=False)
    for key,model in selected_models.items():
        joblib.dump({"kind":key[1],"features":model.features,"target":model.target,"config_id":model.config_id,"model":model.model},model_dir/f"{key[0]}__{key[1]}.joblib")
    primary_metrics=pd.DataFrame(final_rows);r=primary_metrics.query("target=='target_relative_vol_8h' and split=='test'").set_index("objective")
    primary_effect=float(r.loc["ranking","top_1"]-r.loc["regression","top_1"]);ci=next(x for x in effects if x["comparison"]=="ranker_minus_regressor" and x["block_hours"]==24)
    wfdf=pd.DataFrame(wf);positive_folds=int((wfdf.query("objective=='paired_effect'").top_1>0).sum());largest=float(symbol.selection_share.max())
    promising=primary_effect>=.02 and ci["ci_low"]>0 and positive_folds>=2 and largest<.5
    status="PROMISING_RANK_SIGNAL" if promising else ("WEAK" if primary_effect>0 else "NO_SIGNAL")
    summary={"status":status,"research_only":True,"test_provenance":"reused M15.3 research holdout","library":"scikit-learn 1.9.1","seed":SEED,"features":86,"targets":["target_relative_vol_8h","path_quality_score"],"selected_configs":{f"{k[0]}__{k[1]}":v.config_id for k,v in selected_models.items()},"primary_test":r.reset_index().to_dict("records"),"primary_ranker_minus_regressor":ci,"positive_walk_forward_effect_folds":positive_folds,"largest_symbol_selection_share":largest,"winner_frequency_train":winner_frequency,"economic":economics,"deviations":["XGBoost unavailable; used predeclared scikit-learn matched pairwise/regression alternative.","M15.3 row predictions absent; reconstructed by loading frozen model without fit."]}
    (output/"summary.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8")
    (output/"summary.md").write_text(f"# M16.1 Ranking Objective\n\n- Status: **{status}**\n- Ranker minus regressor Top-1: {primary_effect:.4%}\n- 24h block CI: [{ci['ci_low']:.4%}, {ci['ci_high']:.4%}]\n- Reused research holdout; not independent confirmation.\n",encoding="utf-8")
    return summary


def finalize_existing(output=REPORTS, model_dir=MODEL_DIR):
    """Finalize artifacts from the completed run; does not fit or select models."""
    output,model_dir=Path(output),Path(model_dir);frame,_,_=load_data();val=frame[frame.split=="validation"];test=frame[frame.split=="test"]
    models={obj:joblib.load(model_dir/f"target_relative_vol_8h__{obj}.joblib") for obj in ("ranking","regression")}
    economics=[];ledgers=[];importance=[]
    for obj,model in models.items():
        ranked=rank_frame(test,model.predict(test),"target_relative_vol_8h");m,l=fixed_schedule(ranked,obj);economics.append(m);ledgers.append(l)
        importance.extend(importance_rows(model,val,obj))
    frozen=joblib.load(ROOT/"data/models/nextgen_relative_8h_v1/model.joblib")
    ranked=rank_frame(test,frozen["model"].predict(test[frozen["features"]]),"target_relative_vol_8h");m,l=fixed_schedule(ranked,"frozen_m15_3_no_threshold");economics.append(m);ledgers.append(l)
    for name,column in (("momentum","momentum_4_1h"),("relative_strength","return_rank_24h")):
        ranked=rank_frame(test,test[column].to_numpy(),"target_relative_vol_8h");m,l=fixed_schedule(ranked,name);economics.append(m);ledgers.append(l)
    economics.append({"method":"cash","trades":0,"net_return":0.,"pnl":0.,"max_drawdown":0.})
    pd.DataFrame(economics).to_csv(output/"economic_diagnostics.csv",index=False);pd.concat(ledgers).to_csv(output/"economic_ledger.csv",index=False)
    pd.DataFrame(importance).sort_values(["objective","importance"],ascending=[True,False]).to_csv(output/"feature_importance.csv",index=False)
    for path in model_dir.glob("*.joblib"):
        obj=joblib.load(path);joblib.dump({"kind":"ranking" if "__ranking" in path.name else "regression","features":obj.features,"target":obj.target,"config_id":obj.config_id,"model":obj.model},path)
    summary=json.loads((output/"summary.json").read_text());summary["economic"]=economics;summary["artifact_hashes"]={p.name:sha256(p) for p in model_dir.glob("*.joblib")};(output/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--output",default=str(REPORTS));parser.add_argument("--model-dir",default=str(MODEL_DIR));parser.add_argument("--finalize-existing",action="store_true");args=parser.parse_args(argv)
    result=finalize_existing(args.output,args.model_dir) if args.finalize_existing else run(args.output,args.model_dir)
    print(json.dumps(result,indent=2,default=str))


if __name__ == "__main__": main()

