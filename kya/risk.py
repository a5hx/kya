"""Explainable behavioural risk scoring (verifier layer L8).

Hand-weighted features against the user's own history. Deliberately simple:
every point on the score maps to a sentence a reviewer can read.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from .store import Store

IST = timezone(timedelta(hours=5, minutes=30))
STEP_UP_THRESHOLD = 50
VELOCITY_WINDOW_S = 600
VELOCITY_MAX = 3


def assess(store: Store, mandate: dict[str, Any], account: str, merchant: dict[str, Any], amount: int,
           now: int) -> dict[str, Any]:
    scope = mandate["scope"]
    history = store.history(account)
    # Baseline = what the *human* normally does. Agent payments are excluded so a
    # hijacked agent cannot drift its own baseline upward (baseline poisoning).
    human = [t for t in history if t["source"] == "history"]
    same_mcc = [t["amount_inr"] for t in human if t["mcc"] == merchant["mcc"]]
    base = same_mcc if len(same_mcc) >= 3 else [t["amount_inr"] for t in human]
    median = statistics.median(base) if base else None
    seen_merchants = {t["merchant_id"] for t in history}
    recent = store.agent_txn_count_since(mandate["mandate_id"], now - VELOCITY_WINDOW_S)
    local = datetime.fromtimestamp(now, IST)

    features: list[dict[str, Any]] = []

    def feature(name: str, hit: bool, points: int, detail: str) -> None:
        features.append({"name": name, "hit": hit, "points": points if hit else 0, "detail": detail})

    threshold = scope["step_up_above_inr"]
    feature("above_step_up_limit", amount > threshold, 50,
            f"₹{amount:,} vs step-up limit ₹{threshold:,} (AFA-style)")
    ratio = amount / median if median else 0.0
    feature("unusual_amount", bool(median) and ratio >= 3, 30,
            f"{ratio:.1f}× usual spend in this category (median ₹{median:,.0f})" if median else "no baseline")
    feature("new_merchant", merchant["merchant_id"] not in seen_merchants, 20,
            f"first payment to {merchant['name']}" if merchant["merchant_id"] not in seen_merchants
            else f"paid {merchant['name']} before")
    feature("velocity", recent >= VELOCITY_MAX, 50,
            f"{recent} agent payments in the last {VELOCITY_WINDOW_S // 60} min")
    feature("odd_hour", 0 <= local.hour < 5, 25, f"local time {local:%H:%M} IST")

    score = sum(f["points"] for f in features)
    return {"score": score, "threshold": STEP_UP_THRESHOLD, "features": features,
            "baseline_median_inr": median}
