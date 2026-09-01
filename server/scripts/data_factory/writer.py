"""CSV 输出器。

造数阶段的产物统一落盘为 CSV（UTF-8，无 BOM），再由 db_init 用
PostgreSQL COPY 高速装载。这样造数过程不依赖数据库，可独立运行与校验。
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, Iterable, List, Sequence


class CsvWriter:
    def __init__(self, out_dir: str) -> None:
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self._files: Dict[str, Any] = {}
        self._writers: Dict[str, Any] = {}
        self.counts: Dict[str, int] = {}

    def table(self, name: str, columns: Sequence[str]) -> None:
        """声明一张输出表（同名只声明一次）。"""
        if name in self._writers:
            return
        path = os.path.join(self.out_dir, f"{name}.csv")
        f = open(path, "w", encoding="utf-8", newline="")
        w = csv.writer(f)
        w.writerow(list(columns))
        self._files[name] = f
        self._writers[name] = w
        self.counts[name] = 0

    def row(self, name: str, values: Sequence[Any]) -> None:
        w = self._writers.get(name)
        if w is None:
            raise KeyError(f"未声明的输出表：{name}")
        w.writerow(["" if v is None else v for v in values])
        self.counts[name] += 1

    def rows(self, name: str, batch: Iterable[Sequence[Any]]) -> None:
        for values in batch:
            self.row(name, values)

    def close(self) -> None:
        for f in self._files.values():
            f.close()
        self._files.clear()
        self._writers.clear()

    def write_manifest(self, manifest: Dict[str, Any]) -> None:
        path = os.path.join(self.out_dir, "_manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    def summary(self) -> List[str]:
        width = max((len(k) for k in self.counts), default=10)
        return [
            f"  {k.ljust(width)} : {v:,} 行"
            for k, v in sorted(self.counts.items())
        ]
