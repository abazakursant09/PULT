"""PULT-LAUNCH-2.5E-2 — observation-history retention (change-only price/promotion observations).

Feature-flagged OFF (observation_retention_enabled). No scheduler, no endpoint, no provider write.
"""
from .observation_sweep import RetentionResult, run_observation_retention

__all__ = ["RetentionResult", "run_observation_retention"]
