"""Bursty workload for Online Boutique (Cap_3 sec:patrones de carga).

Shape: 30 minutes total. Baseline of N users, with periodic 10-second spikes
to 3N every 60 seconds. Designed to expose bottleneck ping-pong: PBScaler
scales up on a spike, scale-down hits during the cooldown gap, then the
next spike triggers another scale-up.

    every 60 s:
        0  – 50 s  : N  users  (baseline)
        50 – 60 s  : 3N users  (10-second spike)

Total spikes in 30 min = 30. N = BASE_USERS, 3N = 600 users.

Stochastic only via task RNG (seeded). The shape itself is deterministic.
"""

import random
from locust import LoadTestShape

from _common import BASE_USERS, BoutiqueUser, get_seed


class BurstyUser(BoutiqueUser):
    pass


_seed = get_seed()
if _seed is not None:
    random.seed(_seed)


class BurstyShape(LoadTestShape):
    """N users with 10 s @ 3N spikes every 60 s, for 30 min."""

    TOTAL_S = 1800            # 30 min
    PERIOD_S = 60             # one spike per minute
    SPIKE_DURATION_S = 10     # last 10 s of each period
    LOW_USERS = BASE_USERS
    HIGH_USERS = BASE_USERS * 3

    def tick(self):
        t = self.get_run_time()
        if t >= self.TOTAL_S:
            return None
        phase = t % self.PERIOD_S
        if phase >= (self.PERIOD_S - self.SPIKE_DURATION_S):
            return self.HIGH_USERS, 60  # spawn fast for the 10 s spike
        return self.LOW_USERS, 30
