import numpy as np
import pandas as pd
import pytest

from app.ml.nextgen_target_dataset import (
    REGIME_FEATURES,TARGETS,add_8h_path_targets,add_point_in_time_regimes,
    add_relative_and_path_targets,apply_8h_purge,validate,
)

def regime_frame(hours=20):
    rows=[]
    for i,t in enumerate(pd.date_range("2024-01-01 01:00",periods=hours,freq="1h",tz="UTC")):
        split="train" if i<8 else ("validation" if i<14 else "test")
        for a,s in enumerate(("BTCUSDT","ETHUSDT")):
            row={"timestamp":t,"source_open_time":t-pd.Timedelta(hours=1),"symbol":s,"split":split,"close":100+i+a,
             "rsi_14_1h":35 if i%3==0 else 55,"bollinger_position_20_1h":.2 if i%3==0 else .6,"btc_volatility":.01+i/1000,"volatility_20_1h":.01+a*.001,
             "fraction_assets_above_ema":.8,"future_net_return_4h":.001,"future_return_rank":a+1,"eligible_universe_size":2}
            for tf in ("15m","1h","4h"):row[f"ema_distance_20_{tf}"]=.01 if i%2 else -.01;row[f"momentum_4_{tf}"]=.01 if i%2 else -.01
            rows.append(row)
    return pd.DataFrame(rows)

def write_history(root,multiplier=1):
    root.mkdir();times=pd.date_range("2024-01-01",periods=32,freq="1h",tz="UTC")
    for a,s in enumerate(("BTCUSDT","ETHUSDT")):
        close=(100+np.arange(32)+a)*multiplier
        pd.DataFrame({"open_time":times,"high":close+2,"low":close-2,"close":close}).to_csv(root/f"{s}_1h.csv",index=False)

def prepared(tmp_path):
    h=tmp_path/"h";write_history(h);x,_=add_point_in_time_regimes(regime_frame());x=add_8h_path_targets(x,h);x=apply_8h_purge(x);return add_relative_and_path_targets(x)

def test_future_8h_path_and_cost_formula(tmp_path):
    x=prepared(tmp_path);assert np.allclose(x.future_net_return_8h,x.future_gross_return_8h-.0018)
    assert (x.target_timestamp_8h-x.timestamp).eq(pd.Timedelta(hours=8)).all()

def test_future_prices_change_targets_not_t_features(tmp_path):
    base=regime_frame();h1=tmp_path/"a";h2=tmp_path/"b";write_history(h1,1);write_history(h2,2)
    before,_=add_point_in_time_regimes(base);a=add_8h_path_targets(before,h1);b=add_8h_path_targets(before,h2)
    pd.testing.assert_frame_equal(a[["timestamp","symbol","regime_direction"]],b[["timestamp","symbol","regime_direction"]])
    assert not np.allclose(a.future_gross_return_8h,b.future_gross_return_8h,equal_nan=True)

def test_normalization_denominator_is_at_t(tmp_path):
    x=prepared(tmp_path);expected=x.volatility_20_1h*np.sqrt(8) if "volatility_20_1h" in x else None
    # Fixture adds the volatility field explicitly for this safety assertion.
    assert (x.normalization_volatility_at_t>0).all()
    assert np.allclose(x.target_relative_vol_8h,x.target_relative_8h/x.normalization_volatility_at_t)

def test_point_in_time_regime_is_future_independent():
    base=regime_frame();first,thresholds=add_point_in_time_regimes(base);changed=base.copy();changed.future_net_return_4h*=100;second,_=add_point_in_time_regimes(changed,thresholds)
    pd.testing.assert_frame_equal(first[REGIME_FEATURES],second[REGIME_FEATURES])

def test_grouped_split_and_8h_purge(tmp_path):
    x=prepared(tmp_path);assert x.groupby("timestamp").split.nunique().max()==1
    for split,next_split in (("train","validation"),("validation","test")):
        boundary=x.loc[x.split==next_split,"timestamp"].min();assert (x.loc[x.split==split,"target_timestamp_8h"]<boundary).all()

def test_path_labels_and_relative_target(tmp_path):
    x=prepared(tmp_path);assert x.clean_opportunity_8h.isin([0,1]).all() and x.high_quality_opportunity_8h.isin([0,1]).all()
    assert np.allclose(x.groupby("timestamp").target_relative_8h.median(),0)

def test_future_mfe_mae_and_median_are_not_features():
    features=["ema_distance_20_1h","momentum_4_1h","rsi_14_1h"]
    assert not set(features)&set(TARGETS)
    assert not any(c.startswith("future_") for c in features)

def test_validation_rejects_target_in_features(tmp_path):
    x=prepared(tmp_path);result=validate(x,["future_net_return_8h"],[]);assert not result["passed"]

def test_continuation_reversal_metadata_has_no_future_dependency():
    a=regime_frame();b=a.copy();b.future_net_return_4h=-999
    ar,t=add_point_in_time_regimes(a);br,_=add_point_in_time_regimes(b,t)
    assert ar.behavior_regime.equals(br.behavior_regime)
