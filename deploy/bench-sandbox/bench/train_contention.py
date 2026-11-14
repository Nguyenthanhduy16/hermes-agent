"""Typical agent training step on ml-competition-008: XGBoost hist + RandomForest, all cores.

Measures CPU contention between tasks sharing one sandbox (docs/thiet-ke-thuc-nghiem.md):

    docker run -d --name cpubench --cpus 12 --memory 5g --network none \\
        -v ~/datasets/da-code/da_code/source:/data:ro -v $PWD/deploy/bench-sandbox/bench:/bench:ro \\
        hermes-bench-sandbox:2 sleep infinity
    docker exec cpubench python3 /bench/train_contention.py solo
    for i in 1 2 3; do docker exec cpubench python3 /bench/train_contention.py par$i & done; wait
"""
import os
import sys
import time

import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score
from xgboost import XGBRegressor

tag = sys.argv[1] if len(sys.argv) > 1 else "x"
t0 = time.time()
df = pd.read_csv("/data/ml-competition-008/train.csv")
X, y = df.drop(columns=["id", "FloodProbability"]), df["FloodProbability"]
split = int(len(df) * 0.8)
t_load = time.time() - t0

t1 = time.time()
xgb = XGBRegressor(n_estimators=300, max_depth=6, learning_rate=0.1, tree_method="hist")
xgb.fit(X[:split], y[:split])
r2_xgb = r2_score(y[split:], xgb.predict(X[split:]))
t_xgb = time.time() - t1

t2 = time.time()
rf = RandomForestRegressor(n_estimators=100, max_depth=12, n_jobs=-1, random_state=0)
rf.fit(X[:200_000], y[:200_000])
r2_rf = r2_score(y[split:], rf.predict(X[split:]))
t_rf = time.time() - t2

print(f"{tag} load {t_load:.1f}s xgb {t_xgb:.1f}s rf {t_rf:.1f}s total {time.time() - t0:.1f}s "
      f"(r2 {r2_xgb:.3f}/{r2_rf:.3f}, cpu_count {os.cpu_count()})", flush=True)
