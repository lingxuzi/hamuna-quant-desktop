"""AI 策略生成稳定性探针 — 多个策略方向跑通「生成 → 静态校验 → 实盘口径实跑」。

用法:
  cd backend && uv run python scripts/ai_strategy_stability.py
  cd backend && uv run python scripts/ai_strategy_stability.py --only ma_golden macd_cross
  cd backend && uv run python scripts/ai_strategy_stability.py --json /tmp/ai_stability.json

每个方向走真实链路 (与 POST /api/strategies/build step1 同款):
  build_step1 提示词 → AIStrategyGenerator.generate (含一次结构自修复)
  → verify_and_repair (静态校验 + 实盘口径实跑, 跑不通把报错喂回模型改)

草稿落临时目录, 不写用户的 data/strategies/ ; 行情只读真实 DATA_DIR。
退出码 0 = 全部方向实跑通过。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.strategy import _normalize_strategy_meta
from app.data_providers.repository import DataStore, KlineRepository
from app.strategy.ai_generator import AIStrategyGenerator
from app.strategy.ai_verify import verify_and_repair
from app.strategy.prompt_builder import build_step1

# 策略方向矩阵: 覆盖不同指标族 (MA/RSI/BOLL/MACD/量/新高) × 不同执行后端
# (当日快照 polars_expr ↔ 跨日面板 python_history_legacy) × 不同过滤复杂度。
# 每一项都写成用户真会在输入框里写的样子。
DIRECTIONS: list[dict] = [
    {
        "key": "ma_golden",
        "name": "5日线上穿10日线",
        "description": "MA5 上穿 MA10 时买入, MA5 下穿 MA10 时卖出",
        "rules": (
            "1. MA5 上穿 MA10 (金叉) 当日买入\n"
            "2. MA5 下穿 MA10 (死叉) 当日卖出\n"
            "3. 最长持有 20 个交易日, 止损 -5%"
        ),
        "basic_filter": {
            "price_min": 5, "price_max": 200,
            "float_cap_min": 3000000000, "float_cap_max": 150000000000,
            "turnover_min": 1, "exclude_st": True,
            "boards": ["沪主板", "深主板", "创业板", "科创板"],
        },
    },
    {
        "key": "rsi_rebound",
        "name": "RSI超卖反弹",
        "description": "RSI14 低于 30 后回升时买入, 涨到 60 以上或止损卖出",
        "rules": (
            "1. RSI14 < 30 (超卖) 且当日收盘价高于开盘价, 次日开盘买入\n"
            "2. RSI14 > 60 卖出\n"
            "3. 止损 -8%, 最长持有 15 个交易日"
        ),
        "basic_filter": {
            "price_min": 3, "price_max": 150,
            "float_cap_min": 2000000000, "float_cap_max": 100000000000,
            "turnover_min": 0.5, "exclude_st": True,
            "boards": ["沪主板", "深主板", "创业板"],
        },
    },
    {
        "key": "boll_breakout",
        "name": "布林带放量突破",
        "description": "收盘价突破布林上轨且量比放大时买入",
        "rules": (
            "1. 收盘价 > 布林上轨 (boll_upper) 且量比 (vol_ratio_5d) > 1.5 时买入\n"
            "2. 收盘价跌破 20 日均线 (ma20) 卖出\n"
            "3. 止损 -6%, 最长持有 20 个交易日"
        ),
        "basic_filter": {
            "price_min": 5, "price_max": 300,
            "float_cap_min": 3000000000, "float_cap_max": 200000000000,
            "turnover_min": 1, "exclude_st": True,
            "boards": ["沪主板", "深主板", "创业板", "科创板"],
        },
    },
    {
        "key": "macd_cross",
        "name": "MACD金叉",
        "description": "MACD DIF 上穿 DEA 时买入, DIF 下穿 DEA 时卖出",
        "rules": (
            "1. MACD DIF (macd_dif) 上穿 DEA (macd_dea) 当日买入\n"
            "2. MACD DIF 下穿 DEA 当日卖出\n"
            "3. 止损 -7%, 最长持有 30 个交易日"
        ),
        "basic_filter": {
            "price_min": 5, "price_max": 200,
            "float_cap_min": 3000000000, "float_cap_max": 150000000000,
            "turnover_min": 1, "exclude_st": True,
            "boards": ["沪主板", "深主板", "创业板", "科创板"],
        },
    },
    {
        "key": "volume_price",
        "name": "量价齐升",
        "description": "放量上涨, 量比放大且涨幅超过 3% 时买入",
        "rules": (
            "1. 量比 (vol_ratio_5d) > 2 且当日涨幅 (change_pct) > 0.03 时买入\n"
            "2. 收盘价跌破 ma10 卖出\n"
            "3. 止损 -5%, 最长持有 10 个交易日"
        ),
        "basic_filter": {
            "price_min": 3, "price_max": 100,
            "float_cap_min": 1000000000, "float_cap_max": 80000000000,
            "turnover_min": 2, "exclude_st": True,
            "boards": ["沪主板", "深主板", "创业板", "科创板"],
        },
    },
    {
        "key": "new_high_60d",
        "name": "创60日新高",
        "description": "收盘价创 60 日新高时买入, 跌破 20 日均线卖出",
        "rules": (
            "1. 收盘价创 60 日新高 (high_60d) 时买入\n"
            "2. 收盘价跌破 20 日均线 (ma20) 时卖出\n"
            "3. 止损 -6%, 最长持有 20 个交易日"
        ),
        "basic_filter": {
            "price_min": 5, "price_max": 250,
            "float_cap_min": 3000000000, "float_cap_max": 150000000000,
            "turnover_min": 1, "exclude_st": True,
            "boards": ["沪主板", "深主板", "创业板", "科创板"],
        },
    },
    {
        "key": "first_limit_up",
        "name": "首板打板",
        "description": "当日首次涨停时买入, 次日冲高回落卖出",
        "rules": (
            "1. 当日涨停 (signal_limit_up) 且此前没有连续涨停 (consecutive_limit_ups <= 1) 时买入\n"
            "2. 次日收盘价跌破前一日收盘价时卖出\n"
            "3. 止损 -5%, 最长持有 3 个交易日"
        ),
        "basic_filter": {
            "price_min": 3, "price_max": 100,
            "float_cap_min": 1000000000, "float_cap_max": 100000000000,
            "turnover_min": 2, "exclude_st": True,
            "boards": ["沪主板", "深主板", "创业板", "科创板"],
        },
    },
]


_BACKEND_RE = re.compile(r"""EXECUTION_BACKEND\s*=\s*["']([\w_]+)["']""")


def _backend_of(code: str) -> str | None:
    """执行后端写在模块常量里, 不在 META 中 → 从代码里取。"""
    match = _BACKEND_RE.search(code)
    return match.group(1) if match else None


async def _probe_direction(direction: dict, repo) -> dict:
    """跑一个方向, 返回结果字典。"""
    key = direction["key"]
    strategy_id = f"ai_stab_{key}"
    prompt = build_step1(
        direction["name"],
        direction["description"],
        "long",
        direction["rules"],
        strategy_id=strategy_id,
        execution_backend="auto",
        basic_filter=direction.get("basic_filter"),
    )
    generator = AIStrategyGenerator(execution_backend="auto")
    # generate() 内部已含一次结构自修复 (与 /api/strategies/build 同口径)。
    result = await generator.generate(prompt)
    repaired = False
    # 生成后按实盘口径跑一遍, 跑不通把报错喂回模型改 (产品同款闭环)。
    if result.get("valid"):
        result = await verify_and_repair(generator, result, repo=repo)
        repaired = result.get("verify_rounds", 0) > 0

    record = {
        "key": key,
        "name": direction["name"],
        "valid": bool(result.get("valid")),
        "repaired": repaired,
        "error": result.get("error"),
        "backend": None,
        "smoke_ok": None,
        "smoke_issues": [],
        "hits": None,
    }
    if not result.get("valid"):
        record["code"] = result.get("code") or ""
        return record

    code = _normalize_strategy_meta(
        result["code"], strategy_id, direction["name"], direction["description"]
    )
    record["backend"] = _backend_of(result.get("code") or "")
    smoke = result.get("smoke") or {}
    record["smoke_ok"] = bool(smoke.get("ok"))
    record["smoke_issues"] = [
        str(issue.get("message") or issue.get("code") or "")
        for issue in smoke.get("issues") or []
    ]
    record["hits"] = smoke.get("hits")
    record["smoke_skipped"] = smoke.get("skipped")
    if not record["smoke_ok"]:
        record["code"] = code
    return record


async def main_async(args: argparse.Namespace) -> int:
    directions = DIRECTIONS
    if args.only:
        wanted = set(args.only)
        directions = [d for d in DIRECTIONS if d["key"] in wanted]
        if not directions:
            print(f"没有匹配的方向: {sorted(wanted)}", file=sys.stderr)
            return 2

    store = DataStore()
    repo = KlineRepository(store)

    records = []
    for direction in directions:
        print(f"== {direction['key']} ({direction['name']}) ==", flush=True)
        try:
            record = await _probe_direction(direction, repo)
        except Exception as exc:  # noqa: BLE001 — 单个方向失败不该中断整轮
            record = {
                "key": direction["key"], "name": direction["name"],
                "valid": False, "repaired": False,
                "error": f"{type(exc).__name__}: {exc}",
                "backend": None, "smoke_ok": None, "smoke_issues": [], "hits": None,
            }
        records.append(record)
        status = "PASS" if record["smoke_ok"] else (
            "VALID" if record["valid"] else "FAIL"
        )
        print(f"   {status}  backend={record['backend']} "
              f"hits={record['hits']} repaired={record['repaired']}", flush=True)
        if record["error"]:
            print(f"   error: {record['error']}", flush=True)
        for issue in record["smoke_issues"]:
            print(f"   smoke: {issue}", flush=True)
        if not record["smoke_ok"] and record.get("code"):
            print("   ---- 生成代码 ----", flush=True)
            for line in record["code"].splitlines():
                print(f"   | {line}", flush=True)
            print("   ------------------", flush=True)

    passed = sum(1 for r in records if r["smoke_ok"])
    valid = sum(1 for r in records if r["valid"])
    print(f"\n=== 汇总: {len(records)} 个方向 | 校验通过 {valid} | 实跑通过 {passed} ===")
    for r in records:
        if not r["smoke_ok"]:
            print(f"  FAIL {r['key']}: {r['error'] or '; '.join(r['smoke_issues'])}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"明细已写入 {args.json}")
    return 0 if passed == len(records) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 策略生成稳定性探针")
    parser.add_argument("--only", nargs="*", help="只跑指定方向 key")
    parser.add_argument("--json", help="把明细写到该路径")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
