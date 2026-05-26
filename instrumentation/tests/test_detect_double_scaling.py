"""Synthetic tests for detect_double_scaling (Cap_3, failure mode 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from detect_double_scaling import (
    annotate_double_scaling,
    extract_scale_ups,
)


def _ts(offset_s: int) -> pd.Timestamp:
    return pd.Timestamp(datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)) + pd.Timedelta(seconds=offset_s)


def _instances(events: list[tuple[int, dict[str, int]]]) -> pd.DataFrame:
    rows = []
    for offset, replicas in events:
        row: dict[str, object] = {"timestamp": _ts(offset)}
        for svc, n in replicas.items():
            row[f"{svc}&count"] = n
        rows.append(row)
    return pd.DataFrame(rows)


def _phantom(rows: list[tuple[int, str, int, int]]) -> pd.DataFrame:
    """rows = list of (offset_s, service, declared, ready)."""
    return pd.DataFrame(
        [
            {
                "timestamp": _ts(t),
                "service": svc,
                "declared": decl,
                "ready": rdy,
                "delta": decl - rdy,
            }
            for t, svc, decl, rdy in rows
        ]
    )


def test_extract_scale_ups_picks_only_positives():
    df = _instances(
        [
            (0,   {"adservice": 1}),
            (60,  {"adservice": 2}),  # +1 → scale-up
            (120, {"adservice": 1}),  # -1 → scale-down (ignored)
        ]
    )
    events = extract_scale_ups(df)
    assert len(events) == 1
    assert events.iloc[0]["service"] == "adservice"
    assert events.iloc[0]["from_replicas"] == 1
    assert events.iloc[0]["to_replicas"] == 2


def test_annotate_double_scaling_flags_when_phantom_in_window():
    instances = _instances(
        [
            (0,  {"checkout": 1}),
            (30, {"checkout": 2}),    # first scale-up
            (60, {"checkout": 3}),    # second scale-up — should be flagged
        ]
    )
    # Phantom: at t=45, the new replica from the t=30 scale-up is still not Ready
    phantom = _phantom(
        [
            (0,  "checkout", 1, 1),
            (30, "checkout", 2, 1),  # delta=1 — phantom!
            (45, "checkout", 2, 1),  # still phantom 15s before the t=60 scale-up
            (60, "checkout", 3, 1),  # at scale-up moment: declared=3 ready=1
        ]
    )

    scale_ups = extract_scale_ups(instances)
    annotated = annotate_double_scaling(scale_ups, phantom, window_s=60)

    # The first scale-up (t=30) had no prior phantom for its own service in [t-60, t).
    # The second scale-up (t=60) had phantom > 0 in [t-60, t).
    assert annotated.loc[annotated["timestamp"] == _ts(30), "had_phantom"].iloc[0] == False
    assert annotated.loc[annotated["timestamp"] == _ts(60), "had_phantom"].iloc[0] == True


def test_annotate_double_scaling_does_not_flag_when_window_clean():
    instances = _instances(
        [
            (0,   {"adservice": 1}),
            (300, {"adservice": 2}),
        ]
    )
    # Phantom belongs to a DIFFERENT service — must not contaminate adservice
    phantom = _phantom(
        [
            (290, "frontend", 3, 2),  # delta=1 but wrong service
        ]
    )
    scale_ups = extract_scale_ups(instances)
    annotated = annotate_double_scaling(scale_ups, phantom, window_s=60)
    assert annotated["had_phantom"].iloc[0] == False


def test_window_lookback_is_strict_lower_bound():
    """delta > 0 exactly at t = scale_up - window should NOT count
    (window is half-open [t-window, t))."""
    instances = _instances(
        [
            (0,  {"frontend": 1}),
            (60, {"frontend": 2}),
        ]
    )
    # Phantom delta>0 only at t=0 (= 60 - 60 → boundary, included by >=)
    # So this delta is included; reorder to test exclusivity at the upper bound:
    phantom = _phantom(
        [
            (60, "frontend", 5, 4),   # at the scale-up timestamp itself — excluded by < t
        ]
    )
    scale_ups = extract_scale_ups(instances)
    annotated = annotate_double_scaling(scale_ups, phantom, window_s=60)
    # Phantom row at exactly t=scale_up should NOT count (strict <).
    assert annotated["had_phantom"].iloc[0] == False
    assert annotated["max_delta_in_window"].iloc[0] == 0
