"""Shared user behaviour and constants for Online Boutique locustfiles.

Endpoints mirror `pbscaler-keff/PBScaler/scripts/locustfile.py` (Sprint 0 baseline).
Each shape file in this directory imports BoutiqueUser and defines its own
LoadTestShape per the Sprint 1 plan (Cap_3 sec:patrones de carga).

Conventions:
    BASE_USERS  — N users that approximate Cap_3 N=100 req/s with wait_time
                  between(1,5). Empirical conversion based on the upstream
                  StagedRampShape that peaks at 200 users for OB.
    LOCUST_SEED — env var consumed by shapes that need RNG (bursty,
                  trace_driven). Read at module import time.
"""

import os
import random
from typing import Final

from locust import HttpUser, between, task


BASE_USERS: Final[int] = 200  # approximates N=100 req/s on Online Boutique


def get_seed() -> int | None:
    """Return LOCUST_SEED from env as int, or None if unset/invalid."""
    raw = os.environ.get("LOCUST_SEED", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


PRODUCTS: Final[tuple[str, ...]] = (
    "0PUK6V6EV0",
    "1YMWWN1N4O",
    "2ZYFJ3GM2N",
    "66VCHSJNUP",
    "6E92ZMYYFZ",
    "9SIQT8TOJO",
    "L9ECAV7KIM",
    "LS4PSXUNUM",
    "OLJCESPC7Z",
)

CURRENCIES: Final[tuple[str, ...]] = ("EUR", "USD", "JPY", "CAD")


class BoutiqueUser(HttpUser):
    """Online Boutique user behaviour copied verbatim from upstream baseline.

    `abstract = True` prevents Locust from instantiating this class directly
    when scanning imported modules. Each locustfile_<shape>.py defines a
    one-line concrete subclass that Locust then runs.
    """

    abstract = True
    wait_time = between(1, 5)

    @task(1)
    def browse_catalog(self) -> None:
        self.client.get("/")

    @task(10)
    def view_product(self) -> None:
        self.client.get("/product/" + random.choice(PRODUCTS))

    @task(2)
    def set_currency(self) -> None:
        self.client.post(
            "/setCurrency", {"currency_code": random.choice(CURRENCIES)}
        )

    @task(3)
    def view_cart(self) -> None:
        self.client.get("/cart")

    @task(2)
    def add_to_cart(self) -> None:
        product = random.choice(PRODUCTS)
        self.client.get("/product/" + product)
        self.client.post(
            "/cart",
            {
                "product_id": product,
                "quantity": random.choice([1, 2, 3, 4, 5, 10]),
            },
        )

    @task(1)
    def checkout(self) -> None:
        product = random.choice(PRODUCTS)
        self.client.get("/product/" + product)
        self.client.post(
            "/cart",
            {
                "product_id": product,
                "quantity": random.choice([1, 2, 3, 4, 5, 10]),
            },
        )
        self.client.post(
            "/cart/checkout",
            {
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
            },
        )
