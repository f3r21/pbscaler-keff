#!/usr/bin/env python3
"""
Workload designed to provoke bottleneck ping-pong.

Pattern: short warm-up, sharp step UP, sustained peak, sharp step DOWN, sustained low.
The DOWN phase is what the original `StagedRampShape` lacks. Without scale-down
opportunity, ping-pong cannot occur even if the autoscaler is misbehaving.

Total runtime: 18 min (1080 s). Adds ~$1.50 to a GKE run on e2-standard-4 x 3.

Usage:
    locust -f code/benchmarks/locustfile_pingpong.py --headless \
        --host "http://${FRONTEND_IP}" \
        --run-time 18m \
        --csv "${OUT_DIR}/locust" --csv-full-history \
        --loglevel WARNING

Stages (elapsed seconds):
     0 - 120    20  users    warm-up at base load
   120 - 180   200  users    sharp step UP (10x in 60s) -- triggers scale-up
   180 - 480   200  users    sustained peak -- replicas stabilize, JIT warms up
   480 - 540    20  users    sharp step DOWN -- now PBScaler will try scale-down
   540 -  900   20  users    sustained low -- ping-pong window opens here
   900 - 1080  100  users    second smaller burst -- second ping-pong opportunity

Why this provokes the three failure modes:
    - phantom capacity:    visible during 120-180 (replicas declared but not Ready)
    - double scaling:      visible during 180-300 (GA may add more before warmup ends)
    - bottleneck ping-pong: visible during 480-900 (scale-down then back up if SLO dips)
"""

import random

from locust import HttpUser, between, task, LoadTestShape


PRODUCTS = [
    "0PUK6V6EV0", "1YMWWN1N4O", "2ZYFJ3GM2N", "66VCHSJNUP",
    "6E92ZMYYFZ", "9SIQT8TOJO", "L9ECAV7KIM", "LS4PSXUNUM",
    "OLJCESPC7Z",
]
CURRENCIES = ["EUR", "USD", "JPY", "CAD"]


class BoutiqueUser(HttpUser):
    wait_time = between(1, 3)

    @task(2)
    def browse_catalog(self):
        self.client.get("/")

    @task(10)
    def view_product(self):
        self.client.get("/product/" + random.choice(PRODUCTS))

    @task(2)
    def set_currency(self):
        self.client.post("/setCurrency", {"currency_code": random.choice(CURRENCIES)})

    @task(3)
    def view_cart(self):
        self.client.get("/cart")

    @task(2)
    def add_to_cart(self):
        product = random.choice(PRODUCTS)
        self.client.get("/product/" + product)
        self.client.post("/cart", {
            "product_id": product,
            "quantity": random.choice([1, 2, 3, 4, 5]),
        })

    @task(1)
    def checkout(self):
        product = random.choice(PRODUCTS)
        self.client.get("/product/" + product)
        self.client.post("/cart", {
            "product_id": product,
            "quantity": random.choice([1, 2, 3, 4, 5]),
        })
        self.client.post("/cart/checkout", {
            "email": "someone@example.com",
            "street_address": "1600 Amphitheatre Parkway",
            "zip_code": "94043",
            "city": "Mountain View",
            "state": "CA",
            "country": "United States",
            "credit_card_number": "4432-8015-6152-0454",
            "credit_card_expiration_month": "1",
            "credit_card_expiration_year": "2039",
            "credit_card_cvv": "672",
        })


class PingPongShape(LoadTestShape):
    """Load shape with explicit step-up and step-down phases."""

    stages = [
        {"duration":  120, "users":  20, "spawn_rate":  5},
        {"duration":  180, "users": 200, "spawn_rate": 30},
        {"duration":  480, "users": 200, "spawn_rate":  5},
        {"duration":  540, "users":  20, "spawn_rate": 30},
        {"duration":  900, "users":  20, "spawn_rate":  5},
        {"duration": 1080, "users": 100, "spawn_rate": 10},
    ]

    def tick(self):
        run_time = self.get_run_time()
        for stage in self.stages:
            if run_time < stage["duration"]:
                return stage["users"], stage["spawn_rate"]
        return None
