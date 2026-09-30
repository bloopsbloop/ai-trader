import numpy as np
import pandas as pd
import pytest

from app.ml.ranking_objective import make_pairwise
from app.ml.ranking_review import corrected_block_bootstrap, pairwise_scores


class AsymmetricPredictor:
    def predict_proba(self,x):
        p=np.clip(.62+.08*x[:,0],.01,.99)
        return np.column_stack([1-p,p])


def group_frame(order=("A","B","C")):
    values={"A":0.,"B":1.,"C":3.};t=pd.Timestamp("2024-01-01",tz="UTC")
    return pd.DataFrame([{"timestamp":t,"symbol":s,"x":values[s],"target":values[s]} for s in order])


def test_controlled_asymmetric_predictor_exposes_row_order_dependence():
    a=group_frame();b=group_frame(("C","B","A"))
    sa=pairwise_scores(AsymmetricPredictor(),a,["x"]);sb=pairwise_scores(AsymmetricPredictor(),b,["x"])
    left=dict(zip(a.symbol,sa));right=dict(zip(b.symbol,sb))
    assert any(abs(left[s]-right[s])>1e-6 for s in left)


def test_pair_builder_contains_only_expected_within_group_differences():
    t=pd.date_range("2024-01-01",periods=2,freq="h",tz="UTC")
    frame=pd.DataFrame([{"timestamp":t[0],"x":0.,"target":0.},{"timestamp":t[0],"x":1.,"target":1.},
                        {"timestamp":t[1],"x":10.,"target":0.},{"timestamp":t[1],"x":13.,"target":1.},{"timestamp":t[1],"x":15.,"target":2.}])
    x,y=make_pairwise(frame,["x"],"target",seed=42)
    assert sorted(abs(x[:,0]).tolist())==[1.,2.,3.,5.]


def paired(differences,start="2024-01-01 00:00Z"):
    times=pd.date_range(start,periods=len(differences),freq="h")
    left=pd.DataFrame({"timestamp":times,"top_1":np.maximum(differences,0)})
    right=pd.DataFrame({"timestamp":times,"top_1":np.maximum(-np.asarray(differences),0)})
    return left,right


def test_corrected_bootstrap_weights_unequal_blocks_by_observation_count():
    left,right=paired([1]*24+[-1])
    result=corrected_block_bootstrap(left,right,reps=100,block_hours=24)
    assert result["point_effect"]==pytest.approx(23/25)
    assert result["min_block_count"]==1 and result["max_block_count"]==24


def test_corrected_bootstrap_rejects_duplicates_and_mismatched_timestamps():
    left,right=paired([1,0,1])
    with pytest.raises(ValueError,match="duplicate"): corrected_block_bootstrap(pd.concat([left,left.iloc[:1]]),right)
    with pytest.raises(ValueError,match="do not match"): corrected_block_bootstrap(left,right.iloc[:-1])


def test_corrected_bootstrap_identical_inputs_and_seed_reproducibility():
    left,_=paired([1,0,1,0]*20);right=left.copy()
    a=corrected_block_bootstrap(left,right,reps=100);b=corrected_block_bootstrap(left,right,reps=100)
    assert a==b and a["point_effect"]==a["ci_low"]==a["ci_high"]==0
