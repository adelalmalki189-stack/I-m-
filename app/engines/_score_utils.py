"""Shared numeric helpers for score-normalization functions across engines
(Contract Engine, Scoring Engine) — kept tiny and dependency-free so every
score in the system goes through the same clamp/interpolate primitives."""
from __future__ import annotations

from decimal import Decimal


def clamp(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, value))


def lerp(x: Decimal, x0: Decimal, y0: Decimal, x1: Decimal, y1: Decimal) -> Decimal:
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
