"""Trace-driven workload for Online Boutique (Cap_3 sec:patrones de carga).

Shape: 60 minutes total. User counts replay a real production trace
(Alibaba cluster-trace-v2018), bucketed per minute, scaled so the peak
matches 3N (= 3 * BASE_USERS = 600 users).

Reads a preprocessed 60-row curve produced by
`data/prepare_alibaba_trace.py`. The raw 800 MB batch_task.csv is too
slow to parse on every Locust startup; preprocess once, replay forever.

Preprocessed CSV schema (data/alibaba_60min_curve.csv):
    minute,raw_arrivals
    0,3843
    ...
    59,4012

If the preprocessed file is missing, this module raises FileNotFoundError
at import time with a clear pointer to the prepare script.

Stochastic only via task RNG (seeded). The shape itself is deterministic
once the curve is fixed.
"""

import csv
import os
import random
from pathlib import Path
from typing import Final

from locust import LoadTestShape

from _common import BASE_USERS, BoutiqueUser, get_seed


class TraceDrivenUser(BoutiqueUser):
    pass


_seed = get_seed()
if _seed is not None:
    random.seed(_seed)


_PREPROCESSED: Final[Path] = Path(__file__).parent / "data" / "alibaba_60min_curve.csv"
_BUCKET_SIZE_S: Final[int] = 60
_TOTAL_BUCKETS: Final[int] = 60  # 60 min × 1 bucket/min
_PEAK_USERS: Final[int] = BASE_USERS * 3


def _load_user_curve() -> list[int]:
    """Read the preprocessed 60-row curve and scale so peak = 3N."""
    if not _PREPROCESSED.exists():
        raise FileNotFoundError(
            f"Preprocessed curve not found at {_PREPROCESSED}. "
            "Run data/prepare_alibaba_trace.py first to generate it from "
            "batch_task.csv (the raw Alibaba dataset)."
        )

    raw: list[int] = []
    with _PREPROCESSED.open("r", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                raw.append(int(row["raw_arrivals"]))
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError(f"malformed row in {_PREPROCESSED}: {row}") from exc

    if len(raw) != _TOTAL_BUCKETS:
        raise ValueError(
            f"{_PREPROCESSED} has {len(raw)} rows, expected {_TOTAL_BUCKETS}"
        )

    peak = max(raw)
    if peak <= 0:
        raise ValueError(f"{_PREPROCESSED} peak is {peak}, cannot scale")
    scale = _PEAK_USERS / float(peak)
    return [max(1, int(round(c * scale))) for c in raw]


# Defer load until Locust instantiates the shape — keeps `import` cheap and
# unaffected by missing data on the dev machine.
_USER_CURVE: list[int] | None = None


def _get_curve() -> list[int]:
    global _USER_CURVE
    if _USER_CURVE is None:
        _USER_CURVE = _load_user_curve()
    return _USER_CURVE


class TraceDrivenShape(LoadTestShape):
    """Replay 60-min slice of Alibaba 2018 trace, peak scaled to 3N."""

    TOTAL_S = 3600
    SPAWN_RATE = 10

    def tick(self):
        t = self.get_run_time()
        if t >= self.TOTAL_S:
            return None
        bucket_idx = min(int(t // _BUCKET_SIZE_S), _TOTAL_BUCKETS - 1)
        users = _get_curve()[bucket_idx]
        return users, self.SPAWN_RATE
