"""Steady ramp workload for Online Boutique (Cap_3 sec:patrones de carga).

Shape: 60 minutes total. Linear ramp from N to 4N users.

    users(t) = N + (4N - N) * (t / TOTAL_S)
        start = N    (200)
        end   = 4N   (800)

Designed to test gradual scale-up. Phantom capacity should accumulate
slowly; double scaling should be rare; ping-pong essentially absent.
This is the workload where PBScaler vanilla should perform best — it
serves as a control to show that the failure modes seen elsewhere are
not artefacts of the autoscaler being broken in general.

Deterministic — no RNG in the shape.
"""

import random
from locust import LoadTestShape

from _common import BASE_USERS, BoutiqueUser, get_seed


class SteadyRampUser(BoutiqueUser):
    pass


_seed = get_seed()
if _seed is not None:
    random.seed(_seed)


class SteadyRampShape(LoadTestShape):
    """Linear ramp from N to 4N over 60 min."""

    TOTAL_S = 3600
    START_USERS = BASE_USERS         # 200
    END_USERS = BASE_USERS * 4       # 800
    SPAWN_RATE = 10

    def tick(self):
        t = self.get_run_time()
        if t >= self.TOTAL_S:
            return None
        progress = t / self.TOTAL_S
        users = self.START_USERS + (self.END_USERS - self.START_USERS) * progress
        return int(round(users)), self.SPAWN_RATE
