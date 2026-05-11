"""Centralised, env-driven runtime settings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    service_name: str
    log_level: str
    log_dir: str
    sim_latency_min: float
    sim_latency_max: float
    seed_on_startup: bool
    seed_customer_count: int
    seed_random_seed: int
    database_name: str

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            service_name=os.getenv("SERVICE_NAME", "CoreBankingAPI"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_dir=os.getenv("LOG_DIR", "/var/logs/bank-simulator"),
            sim_latency_min=float(os.getenv("SIMULATED_LATENCY_MIN", "0.1")),
            sim_latency_max=float(os.getenv("SIMULATED_LATENCY_MAX", "0.4")),
            seed_on_startup=os.getenv("SEED_ON_STARTUP", "true").lower() == "true",
            seed_customer_count=int(os.getenv("SEED_CUSTOMER_COUNT", "50")),
            seed_random_seed=int(os.getenv("SEED_RANDOM_SEED", "42")),
            database_name=os.getenv("DATABASE_NAME", "core_banking"),
        )


settings = Settings.load()
