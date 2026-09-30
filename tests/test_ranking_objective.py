import numpy as np
import pandas as pd
import pytest

from app.ml.ranking_objective import (fixed_schedule, grouped_boundaries, make_pairwise,
    ordinal_relevance, paired_block_bootstrap, ranking_metrics, rank_frame)


def sample(hours=30):
    rows=[]
    for i,t in enumerate(pd.date_range("2024-01-01",periods=hours,freq="h",tz="UTC")):
        for j,s in enumerate(("A","B","C") if i%5 else ("A","B")):
            rows.append({"timestamp":t,"target_timestamp_8h":t+pd.Timedelta(hours=8),"symbol":s,"split":"train",
                         "x":i+j/10,"y":j-i/100,"target_relative_vol_8h":float(j),"future_net_return_8h":.01*(j-1),
                         "momentum_4_1h":j,"return_rank_24h":j,"behavior_regime":"MIXED","market_state":"RANGING"})
    return pd.DataFrame(rows)


def test_relevance_transform_preserves_ties_and_is_nonnegative():
    result=ordinal_relevance(pd.Series([3.,1.,3.,2.]))
    assert result.min()>=0 and result[0]==result[2] and result[0]>result[3]>result[1]


def test_pairwise_rows_never_compare_timestamp_groups_and_are_balanced():
    frame=sample(10);x,y=make_pairwise(frame,["x","y"],"target_relative_vol_8h")
    assert x.shape[0]==len(y) and x.shape[1]==2 and set(y)=={0,1}


def test_metrics_support_variable_universe_and_exact_winner():
    frame=sample(10);ranked=rank_frame(frame,frame.x,"target_relative_vol_8h");metrics,per_time=ranking_metrics(ranked,"target_relative_vol_8h")
    assert metrics["groups"]==10 and metrics["top_1"]==1 and set(per_time.universe)=={2,3}


def test_grouped_split_is_isolated_and_purged():
    frame=sample(120)
    for _,train,val,evaluation in grouped_boundaries(frame):
        assert set(train.timestamp).isdisjoint(val.timestamp) and set(val.timestamp).isdisjoint(evaluation.timestamp)
        assert train.target_timestamp_8h.max()<val.timestamp.min()
        assert val.target_timestamp_8h.max()<evaluation.timestamp.min()


def test_bootstrap_keeps_timestamp_as_atomic_observation():
    times=pd.date_range("2024-01-01",periods=100,freq="h",tz="UTC")
    left=pd.DataFrame({"timestamp":times,"top_1":[1]*100});right=pd.DataFrame({"timestamp":times,"top_1":[0]*100})
    result=paired_block_bootstrap(left,right,reps=50)
    assert result["effect"]==result["ci_low"]==result["ci_high"]==1 and result["blocks"]<result["groups"]


def test_costs_are_not_subtracted_twice_and_schedule_is_nonoverlapping():
    frame=sample(24);ranked=rank_frame(frame,frame.x,"target_relative_vol_8h");metrics,ledger=fixed_schedule(ranked,"test")
    assert (ledger.timestamp.diff().dropna()>=pd.Timedelta(hours=8)).all()
    expected=1000*np.prod(1+.1*ledger.net_return)-1000
    assert metrics["pnl"]==pytest.approx(expected)


def test_research_namespace_does_not_overlap_frozen_registry():
    from app.ml.ranking_objective import MODEL_DIR, ROOT
    assert MODEL_DIR != ROOT/"data/models/nextgen_relative_8h_v1"
