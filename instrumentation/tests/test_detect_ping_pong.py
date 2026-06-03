"""Synthetic tests for detect_ping_pong (Cap_3 sec:nivel1, failure mode 3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from detect_ping_pong import detect_changes, find_ping_pong


def _build_instances(events: list[tuple[int, dict[str, int]]]) -> pd.DataFrame:
    """events = list of (offset_seconds, {svc: replicas})."""
    base = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)
    rows = []
    for offset, replicas in events:
        row: dict[str, object] = {"timestamp": base + timedelta(seconds=offset)}
        for svc, n in replicas.items():
            row[f"{svc}&count"] = n
        rows.append(row)
    return pd.DataFrame(rows)


def test_detect_changes_picks_up_only_diffs():
    df = _build_instances(
        [
            (0,  {"adservice": 1, "frontend": 1}),
            (10, {"adservice": 1, "frontend": 1}),  # no change
            (20, {"adservice": 2, "frontend": 1}),  # +1 ad
            (30, {"adservice": 2, "frontend": 0}),  # -1 frontend
        ]
    )
    events = detect_changes(df)
    assert len(events) == 2
    assert {"adservice", "frontend"} == set(events["service"])
    ad = events[events["service"] == "adservice"].iloc[0]
    assert ad["delta"] == 1
    assert ad["from"] == 1 and ad["to"] == 2


def test_find_ping_pong_pairs_up_then_down_within_window():
    df = _build_instances(
        [
            (0,  {"checkout": 1}),
            (30, {"checkout": 3}),   # scale-up +2
            (90, {"checkout": 1}),   # scale-down -2 within 60 s
        ]
    )
    events = detect_changes(df)
    pp = find_ping_pong(events, window_s=120)
    assert len(pp) == 1
    row = pp.iloc[0]
    assert row["service"] == "checkout"
    assert row["first_delta"] == 2
    assert row["second_delta"] == -2
    assert row["interval_s"] == 60.0


def test_find_ping_pong_ignores_pair_outside_window():
    df = _build_instances(
        [
            (0,   {"checkout": 1}),
            (30,  {"checkout": 3}),   # scale-up
            (300, {"checkout": 1}),   # scale-down 270 s later — outside 120 s window
        ]
    )
    events = detect_changes(df)
    pp = find_ping_pong(events, window_s=120)
    assert pp.empty


def test_find_ping_pong_ignores_two_consecutive_scale_ups():
    df = _build_instances(
        [
            (0,  {"checkout": 1}),
            (30, {"checkout": 2}),  # +1
            (60, {"checkout": 4}),  # +2
        ]
    )
    events = detect_changes(df)
    pp = find_ping_pong(events, window_s=120)
    assert pp.empty


def test_find_ping_pong_does_not_cross_services():
    """An up on adservice followed by a down on cartservice is not a ping-pong."""
    df = _build_instances(
        [
            (0,  {"adservice": 1, "cartservice": 2}),
            (30, {"adservice": 2, "cartservice": 2}),  # +1 ad
            (60, {"adservice": 2, "cartservice": 1}),  # -1 cart
        ]
    )
    events = detect_changes(df)
    pp = find_ping_pong(events, window_s=120)
    assert pp.empty


@pytest.mark.parametrize("interval_s,expect_match", [(50, True), (130, False)])
def test_window_boundary(interval_s: int, expect_match: bool):
    df = _build_instances(
        [
            (0,           {"frontend": 1}),
            (10,          {"frontend": 2}),                  # +1
            (10 + interval_s, {"frontend": 1}),              # -1
        ]
    )
    events = detect_changes(df)
    pp = find_ping_pong(events, window_s=120)
    assert (not pp.empty) is expect_match
