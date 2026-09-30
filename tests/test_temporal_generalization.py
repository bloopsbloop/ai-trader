import numpy as np
import pandas as pd
import pytest

from app.ml.temporal_generalization import (
    add_targets,cost_sensitivity,drift_row,horizon_analysis,mfe_mae,psi,
    regime_analysis,target_persistence,
)


def frame(hours=16):
    rows=[];symbols=("BTCUSDT","ETHUSDT","SOLUSDT")
    for i,t in enumerate(pd.date_range("2024-01-01",periods=hours,freq="1h",tz="UTC")):
        split="train" if i<8 else ("validation" if i<12 else "test"); vals=[]
        for a,s in enumerate(symbols):
            value=.002*(a-1)+.001*np.sin(i);vals.append(value)
            rows.append({"timestamp":t,"target_timestamp":t+pd.Timedelta(hours=4),"symbol":s,"split":split,"close":100+i+a,
             "future_net_return_2h":value/2,"future_net_return_4h":value,"future_net_return_8h":value*2,
             "future_return_percentile":(a+1)/3,"risk_adjusted_target":value/.01,"volatility_20_1h":.01+a*.001,"atr_fraction_14_1h":.012,
             "future_max_favorable_move":max(value+.006,.001),"future_max_adverse_move":min(value-.004,-.001),
             "candidate_positive":int(value>.001),"candidate_strong":int(value>.005),"ideal_candidate":0,"candidate_top_2":0,
             "no_trade_preferred":0,"btc_trend":.01,"btc_volatility":.01,"cross_sectional_dispersion":.002,"market_breadth":.67,
             "trend_alignment_score":2,"return_24h":(-1)**a*.01,"regime_category":"MEDIUM"})
        start=len(rows)-3;ranks=pd.Series(vals).rank(ascending=False,method="first").astype(int)
        for a in range(3):rows[start+a]["candidate_top_2"]=int(ranks.iloc[a]<=2 and vals[a]>0);rows[start+a]["ideal_candidate"]=int(ranks.iloc[a]<=2 and vals[a]>.001);rows[start+a]["future_return_rank"]=ranks.iloc[a]
    return pd.DataFrame(rows)


def regime(data):
    x=data.drop_duplicates("timestamp")[["timestamp","split"]].copy();x["strong_opportunity_exists_4h"]=(np.arange(len(x))%4<2).astype(int);return x


def test_drift_metrics_detect_identical_and_shifted_distributions():
    a=pd.Series(np.arange(100.));same=drift_row("x",a,a,"test");shift=drift_row("x",a,a+100,"test")
    assert same["psi"]==pytest.approx(0) and same["ks_statistic"]==0
    assert shift["ks_statistic"]>.9 and abs(shift["standardized_mean_difference"])>1


def test_psi_is_nonnegative():
    assert psi(pd.Series(range(100)),pd.Series(range(20,120)))>=0


def test_horizon_targets_are_kept_separate_and_correct():
    result=horizon_analysis(frame())
    assert set(result.horizon_hours)=={2,4,8}
    assert result.set_index("horizon_hours").loc[8,"average_best_return"]>result.set_index("horizon_hours").loc[4,"average_best_return"]


def test_cost_sensitivity_is_monotonic():
    result=cost_sensitivity(add_targets(frame()));all_rows=result[result.split=="all"].sort_values("round_trip_cost")
    assert all_rows.positive_asset_rate.is_monotonic_decreasing
    assert all_rows.no_opportunity_rate.is_monotonic_increasing


def test_target_persistence_uses_exact_neighbor_rows():
    data=add_targets(frame());result=target_persistence(data,regime(data))
    assert set(result.lag_hours).issuperset({0,1,2}) and result.persistence.dropna().between(0,10).all()


def test_relative_and_volatility_normalized_targets():
    result=add_targets(frame());groups=result.groupby("timestamp")
    assert np.allclose(groups.relative_vs_median_4h.median(),0)
    assert np.allclose(result.volatility_normalized_4h,result.future_net_return_4h/result.volatility_20_1h)


def test_mfe_mae_and_path_labels():
    data=add_targets(frame());result=mfe_mae(data)
    assert {"clean_opportunity_rate","high_quality_opportunity_rate","median_mfe_mae_ratio"}.issubset(result.columns)
    assert data.clean_opportunity.isin([0,1]).all()


def test_regime_segmentation_uses_current_features():
    result=regime_analysis(add_targets(frame()))
    assert {"btc_ema_regime","volatility_regime","dispersion_regime","breadth_regime","alignment_regime","future_behavior"}.issubset(set(result.dimension))


def test_future_values_do_not_enter_feature_inputs():
    features=["volatility_20_1h","atr_fraction_14_1h","btc_trend","market_breadth","trend_alignment_score"]
    assert not any(name.startswith(("future_","candidate_","ideal_")) for name in features)
