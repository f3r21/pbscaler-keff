"""Step workload for Online Boutique (Cap_3 sec:patrones de carga).

Shape: 30 minutes total.
    0   – 600 s  (10 min)  : N  users  (steady)
    600 – 1800 s (20 min)  : 3N users  (steady, after abrupt jump)

N = BASE_USERS (≈100 req/s with wait_time between(1,5)).
3N = 600 users.

Spawn rate at the jump is high (60 users/s) to approximate an abrupt step,
not a ramp. The point of this workload is to expose phantom capacity and
double scaling on the rising edge.

Deterministic — no RNG in the shape itself.
"""

import random
from locust import LoadTestShape

from _common import BASE_USERS, BoutiqueUser, get_seed


class StepUser(BoutiqueUser):
    """Concrete user class — locust auto-discovers and instantiates this."""


# Seed the user-task RNG so checkout/product picks are reproducible across reps
_seed = get_seed()
if _seed is not None:
    random.seed(_seed)


class StepShape(LoadTestShape):
    """Abrupt N → 3N step at t=600 s."""

    PHASE1_END_S = 600   # 10 min @ N
    PHASE2_END_S = 1800  # 20 min @ 3N
    LOW_USERS = BASE_USERS
    HIGH_USERS = BASE_USERS * 3

    def tick(self):
        t = self.get_run_time()
        if t < self.PHASE1_END_S:
            return self.LOW_USERS, 50  # spawn quickly to reach steady state
        if t < self.PHASE2_END_S:
            # Spawn rate 60/s allows reaching 3N≈600 users in ~7s — abrupt.
            return self.HIGH_USERS, 60
        return None
