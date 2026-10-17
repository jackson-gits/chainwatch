"""Profile the PaySim CSV before use (spec 3.1, 8.3) and write results/paysim_profile.json. Train env.

Checks: row count, fraud rows, unique senders and receivers, median transactions per account, types
where fraud occurs, the isFlaggedFraud rule as a baseline, and how predictive the balance fields are
(AUC of a model using only balance-derived features on a step-based split).

Usage: python scripts/profile_paysim.py [data/raw/paysim]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def profile(df) -> dict:
    import numpy as np

    from chainwatch.eval.metrics import average_precision, roc_auc

    n = len(df)
    fraud = df["isFraud"].astype(int)
    snd = df["nameOrig"].astype(str)
    rcv = df["nameDest"].astype(str)
    per_sender = snd.value_counts()
    per_receiver = rcv.value_counts()
    types_with_fraud = sorted(df.loc[fraud == 1, "type"].astype(str).unique().tolist())
    flagged = df["isFlaggedFraud"].astype(int)
    tp = int(((flagged == 1) & (fraud == 1)).sum())
    out = {
        "provenance": "PaySim",
        "rows": int(n),
        "fraud_rows": int(fraud.sum()),
        "fraud_share": float(fraud.mean()),
        "unique_senders": int(snd.nunique()),
        "unique_receivers": int(rcv.nunique()),
        "share_senders_single_txn": float((per_sender == 1).mean()),
        "median_txns_per_sender": float(per_sender.median()),
        "median_txns_per_receiver": float(per_receiver.median()),
        "types": sorted(df["type"].astype(str).unique().tolist()),
        "types_with_fraud": types_with_fraud,
        "steps": [int(df["step"].min()), int(df["step"].max())],
        "isFlaggedFraud_rule": {
            "flagged": int(flagged.sum()),
            "true_positives": tp,
            "recall": tp / int(fraud.sum()) if int(fraud.sum()) else None,
            "precision": tp / int(flagged.sum()) if int(flagged.sum()) else None,
        },
    }
    # balance-only model: sender drain ratio, sender balance after ~ 0, receiver balance error; step split
    amt = df["amount"].astype(float).to_numpy()
    ob, nb = df["oldbalanceOrg"].astype(float).to_numpy(), df["newbalanceOrig"].astype(float).to_numpy()
    od, nd = df["oldbalanceDest"].astype(float).to_numpy(), df["newbalanceDest"].astype(float).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        drain = np.where(ob > 0, np.clip(amt / ob, 0, 1.5), 0.0)
    X = np.stack(
        [
            drain,
            (nb < 1).astype(float),
            (ob > 0).astype(float),
            ob + amt - nb,
            od + amt - nd,
            (od == 0).astype(float),
        ],
        axis=1,
    )
    step = df["step"].to_numpy()
    tr, te = (step >= 73) & (step <= 400), step >= 501
    y = fraud.to_numpy()
    try:
        import lightgbm as lgb

        b = lgb.train(
            {
                "objective": "binary",
                "verbosity": -1,
                "seed": 2026,
                "deterministic": True,
                "force_row_wise": True,
                "num_threads": 4,
            },
            lgb.Dataset(X[tr], y[tr]),
            num_boost_round=200,
        )
        s = b.predict(X[te])
        out["balance_only_model"] = {
            "features": [
                "sender_drain",
                "sender_after_zero",
                "sender_balance_known",
                "sender_balance_error",
                "receiver_balance_error",
                "receiver_before_zero",
            ],
            "split": "train steps 73-400, test steps 501-744",
            "test_roc_auc": roc_auc(y[te], s),
            "test_pr_auc": average_precision(y[te], s),
            "test_positive_rate": float(y[te].mean()),
        }
    except ImportError:
        out["balance_only_model"] = {"skipped": "lightgbm not installed"}
    return out


def main(argv: list[str]) -> int:
    from chainwatch.data.paysim import find_csv, read_paysim

    src = argv[0] if argv else "data/raw/paysim"
    out_path = ROOT / "results" / "paysim_profile.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path = find_csv(src)
    except FileNotFoundError as e:
        out_path.write_text(
            json.dumps({"provenance": "PaySim", "status": "missing", "message": str(e)}, indent=1),
            encoding="utf-8",
        )
        print(str(e))
        return 2
    df = read_paysim(path)
    prof = profile(df)
    prof["status"] = "ok"
    prof["source_file"] = path.name
    prof["licence"] = "UNVERIFIED (read it on the Kaggle page)"
    out_path.write_text(json.dumps(prof, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {k: prof[k] for k in ("rows", "fraud_rows", "unique_senders", "types_with_fraud")}, indent=1
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
