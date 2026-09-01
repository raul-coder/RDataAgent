"""确定性随机工具（仅标准库）。

所有随机源都来自同一个 random.Random(SEED) 实例，且调用顺序固定，
因此同一版本脚本在任意机器上产出的数据完全一致。
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Sequence, Tuple


class RNG:
    """带业务语义的随机包装器。"""

    def __init__(self, seed: int) -> None:
        self._r = random.Random(seed)

    # ── 基础分布 ────────────────────────────────────────────────────
    def uniform(self, a: float, b: float) -> float:
        return self._r.uniform(a, b)

    def randint(self, a: int, b: int) -> int:
        return self._r.randint(a, b)

    def normal(self, mu: float, sigma: float, lo: float = None, hi: float = None) -> float:
        v = self._r.gauss(mu, sigma)
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    def lognormal(self, mu: float, sigma: float) -> float:
        return self._r.lognormvariate(mu, sigma)

    def beta(self, a: float, b: float) -> float:
        return self._r.betavariate(a, b)

    def bernoulli(self, p: float) -> bool:
        return self._r.random() < p

    def choice(self, seq: Sequence[Any]) -> Any:
        return self._r.choice(seq)

    def poisson(self, lam: float) -> int:
        """Knuth 算法（lam 较小时使用），大 lam 用正态近似。"""
        if lam <= 0:
            return 0
        if lam < 30:
            limit = math.exp(-lam)
            k = 0
            p = 1.0
            while True:
                p *= self._r.random()
                if p <= limit:
                    break
                k += 1
            return k
        return max(0, int(round(self._r.gauss(lam, math.sqrt(lam)))))

    # ── 权重选择 ────────────────────────────────────────────────────
    def weighted_choice(
        self, items: Sequence[Any], weights: Sequence[float]
    ) -> Any:
        """按权重抽取单个元素。weights 不必归一化。"""
        total = sum(weights)
        if total <= 0:
            return items[0]
        x = self._r.random() * total
        acc = 0.0
        for item, w in zip(items, weights):
            acc += w
            if x <= acc:
                return item
        return items[-1]

    def weighted_pick_index(self, weights: Sequence[float]) -> int:
        total = sum(weights)
        if total <= 0:
            return 0
        x = self._r.random() * total
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if x <= acc:
                return i
        return len(weights) - 1

    def sample(self, population: Sequence[Any], k: int) -> List[Any]:
        return self._r.sample(list(population), min(k, len(population)))


def weighted_split(total_cents: int, weights: Sequence[float]) -> List[int]:
    """把整数 total_cents 按权重拆分为整数列表，保证求和严格等于 total_cents。

    采用最大余额法（largest remainder），余额按小数部分从大到小分配，
    同值时按索引升序，保证结果确定。
    """
    n = len(weights)
    if n == 0:
        return []
    wsum = sum(weights)
    if wsum <= 0:
        # 权重全为 0 时平均分配
        base = total_cents // n
        out = [base] * n
        out[0] += total_cents - base * n
        return out

    raw = [total_cents * w / wsum for w in weights]
    floors = [int(math.floor(x)) for x in raw]
    remainder = total_cents - sum(floors)
    order = sorted(
        range(n), key=lambda i: (-(raw[i] - floors[i]), i)
    )
    for i in range(remainder):
        floors[order[i % n]] += 1
    return floors


def to_wan(cents: int) -> str:
    """cents（万元的百分之一）→ 保留 2 位小数的万元字符串。"""
    sign = "-" if cents < 0 else ""
    v = abs(cents)
    return f"{sign}{v // 100}.{v % 100:02d}"
