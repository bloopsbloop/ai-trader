import json
import numpy as np
import pandas as pd
from xgboost import XGBRanker

from app.ml.native_ltr import evaluation,labels,ordered,qids,relevance,score_frame
from app.ml.ranking_objective import grouped_boundaries
from app.ml.ranking_objective import METADATA

def frame():
    rows=[]
    for i,t in enumerate(pd.date_range("2024-01-01",periods=12,freq="h",tz="UTC")):
        symbols=("A","B") if i%3==0 else ("A","B","C")
        for j,s in enumerate(symbols):rows.append({"timestamp":t,"target_timestamp_8h":t+pd.Timedelta(hours=8),"symbol":s,"f1":j+i/10,"f2":i-j/10,"target_relative_vol_8h":float(j//2),"future_net_return_8h":.01*j,"market_state":"RANGING"})
    return pd.DataFrame(rows)

def test_integer_relevance_ties_and_variable_groups():
    assert relevance(np.array([3.,1.,3.,2.])).tolist()==[2,0,2,1]
    x=ordered(frame());assert len(labels(x))==len(x) and qids(x).min()==0 and np.all(np.diff(qids(x))>=0)

def test_qid_groups_are_contiguous_and_predictors_exclude_identity_and_labels():
    x=ordered(frame());ids=qids(x)
    assert all(np.ptp(np.flatnonzero(ids==i))+1==(ids==i).sum() for i in np.unique(ids))
    predictors=["f1","f2"];assert not {"timestamp","symbol","target_relative_vol_8h"}.intersection(predictors)

def test_native_frozen_inference_is_permutation_invariant_with_symbol_tie_break():
    x=ordered(frame());model=XGBRanker(objective="rank:ndcg",ndcg_exp_gain=False,n_estimators=5,max_depth=2,tree_method="hist",n_jobs=1,random_state=42)
    model.fit(x[["f1","f2"]],labels(x),qid=qids(x),verbose=False)
    base=score_frame(model,x,["f1","f2"])[["timestamp","symbol","score"]].sort_values(["timestamp","symbol"])
    shuffled=x.sample(frac=1,random_state=7);other=score_frame(model,shuffled,["f1","f2"])[["timestamp","symbol","score"]].sort_values(["timestamp","symbol"])
    np.testing.assert_allclose(base.score,other.score)
    a,_=evaluation(score_frame(model,x,["f1","f2"]));b,_=evaluation(score_frame(model,shuffled,["f1","f2"]));assert a["top_1"]==b["top_1"]

def test_all_86_features_include_market_context():
    meta=json.loads(METADATA.read_text());features=meta["schemas"]["stability_filtered_features"]
    assert len(features)==86
    for name in ("btc_trend","btc_volatility","market_breadth","cross_sectional_dispersion","median_universe_rsi"):assert name in features

def test_grouped_chronological_folds_isolate_timestamps_and_purge_labels():
    pieces=[]
    for i,t in enumerate(pd.date_range("2024-01-01",periods=120,freq="h",tz="UTC")):
        for symbol in ("A","B"):pieces.append({"timestamp":t,"target_timestamp_8h":t+pd.Timedelta(hours=8),"symbol":symbol})
    x=pd.DataFrame(pieces)
    for _,train,validation,evaluation_part in grouped_boundaries(x):
        assert set(train.timestamp).isdisjoint(validation.timestamp) and set(validation.timestamp).isdisjoint(evaluation_part.timestamp)
        assert train.target_timestamp_8h.max()<validation.timestamp.min()
        assert validation.target_timestamp_8h.max()<evaluation_part.timestamp.min()

def test_top_k_and_linear_ndcg_semantics():
    x=frame().iloc[2:5].copy();x["score"]=[2.,0.,1.];metrics,_=evaluation(x)
    assert metrics["top_1"]==0 and metrics["top_2"]==1 and 0<=metrics["ndcg_at_3"]<=1
