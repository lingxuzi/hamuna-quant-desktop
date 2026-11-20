#!/usr/bin/env python
"""一次性修复脚本：归一化存量除权因子（按明细列重算 ex_factor）。

背景：除权参考价的计算从 float 路径换成 Decimal 后，存量行的因子是旧公式
算出来的 —— 参考价在 x.xx5 半分界上会被上游 float32 噪声带偏 1 分，因子差
0.09%，除权日的涨跌停限价随之差 1 分，收盘贴着限价时会被误判成涨跌停
（实测 556/21665 行 / 470 只标的）。本脚本按存量的明细列（prev_close /
dividend / bonus / allot / allot_price）重算因子，不联网、可重复执行（幂等）。

用法（从 backend/ 目录运行）：
    .venv/bin/python -m scripts.repair_adj_factor            # 归一化并写盘
    .venv/bin/python -m scripts.repair_adj_factor --dry-run   # 只报告, 不写盘
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.config import settings
from app.services.kline_sync import recompute_adj_factor_store

DATA_DIR = Path(settings.data_dir)
STORES = ("adj_factor", "adj_factor_etf")


def main() -> int:
    parser = argparse.ArgumentParser(description="归一化存量除权因子")
    parser.add_argument("--dry-run", action="store_true", help="只报告差异, 不写盘")
    args = parser.parse_args()

    for name in STORES:
        path = DATA_DIR / name / "all.parquet"
        if not path.exists():
            print(f"{name}: 无存量, 跳过")
            continue
        rows, symbols = recompute_adj_factor_store(path, dry_run=args.dry_run)
        print(f"{name}: 因子变化 {rows} 行, 涉及标的 {len(symbols)}"
              + (" (dry-run, 未写盘)" if args.dry_run else ""))
        if symbols:
            shown = "、".join(symbols[:20])
            print("  受影响标的: " + shown + (" …" if len(symbols) > 20 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
