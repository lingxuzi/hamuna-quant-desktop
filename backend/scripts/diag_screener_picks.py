"""选股无命中诊断 — 在目标机器上跑, 只读, 不写任何数据。

用法:
    cd backend && .venv/bin/python scripts/diag_screener_picks.py <strategy_id> [as_of]

输出四段: 数据覆盖 / 策略来源与参数 / 特征列可用性 / 实跑命中数。
同一台机器上跑两次 (本机 vs 服务器) 直接对比, 就能定位差异在哪一段。
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.data_providers.repository import DataStore, KlineRepository
from app.services.screener import ScreenerService
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine


def _partition_dates(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.name.split("=", 1)[1] for p in root.glob("date=*") if p.is_dir())


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    strategy_id = sys.argv[1]
    data_dir = Path(settings.data_dir)

    _section("环境")
    print(f"data_dir 末级目录: {data_dir.name}  存在={data_dir.exists()}")
    if not data_dir.exists():
        return 1

    repo = KlineRepository(DataStore(data_dir))
    engine = StrategyEngine(
        strategy_dirs=[
            Path(__file__).resolve().parents[1] / "app" / "strategy" / "research",
            data_dir / "strategies" / "custom",
            data_dir / "strategies" / "ai",
            data_dir / "strategies" / "composite",
        ]
    )
    if engine.load_errors():
        print("策略加载错误:", engine.load_errors())
    svc = ScreenerService(repo, asset_type="stock")

    _section("数据覆盖")
    enriched_dir = data_dir / "kline_daily_enriched"
    raw_dir = data_dir / "kline_daily"
    enriched_dates = _partition_dates(enriched_dir)
    raw_dates = _partition_dates(raw_dir)
    print(f"enriched 分区: {len(enriched_dates)}  {enriched_dates[:1]} → {enriched_dates[-1:]}")
    print(f"原始日K分区: {len(raw_dates)}  {raw_dates[:1]} → {raw_dates[-1:]}")
    latest = svc.latest_date()
    print(f"选股可用最新日: {latest}")
    if enriched_dates:
        part = enriched_dir / f"date={enriched_dates[-1]}" / "part.parquet"
        if part.exists():
            print(f"最新 enriched 分区行数: {pl.read_parquet(part).height}")

    _section("策略来源与参数")
    if not engine.has(strategy_id):
        print(f"策略 {strategy_id} 未注册。已注册:", sorted(s.meta['id'] for s in engine.strategy_definitions()))
        return 1
    s = engine.get(strategy_id)
    print(f"source={s.source}  backend={s.execution_backend}  文件名={Path(s.file_path).name if s.file_path else '-'}")
    print(f"asset_types={s.meta.get('asset_types')}  scoring列={list(s.scoring_columns)}")
    override = strategy_config.load_override(data_dir, strategy_id)
    ov_path = data_dir / "user_data" / "strategy_overrides" / f"{strategy_id}.json"
    print(f"用户参数覆盖文件: {ov_path.name} 存在={ov_path.exists()}  参数={override.get('params') or {}}")

    _section("用户数据 (不在 git 里, 换机器要单独拷)")
    for rel in ("user_data/watchlist.parquet", "user_data/custom_signals", "ext_data", "strategies/ai"):
        p = data_dir / rel
        print(f"{rel}: 存在={p.exists()}  条目={len(list(p.iterdir())) if p.is_dir() else ('-' if not p.exists() else p.stat().st_size)}")

    _section("特征列可用性")
    as_of = latest
    if as_of is None:
        print("没有可用日期, 后面跑不了")
        return 1
    params = dict(override.get("params") or {})
    context = svc.build_strategy_context(engine, as_of, [strategy_id], params_map={strategy_id: params})
    current = context.current
    history = context.history
    print(f"current 行数={current.height if current is not None else 0}  列数={len(current.columns) if current is not None else 0}")
    print(f"history 行数={history.height if history is not None else 0}  列数={len(history.columns) if history is not None else 0}")
    need = set(s.required_features) | set(s.scoring_columns)
    if s.matrix_strategy is not None:
        try:
            need |= set(s.matrix_strategy.required_fields_for_params(params))
        except Exception as exc:  # noqa: BLE001
            print(f"required_fields_for_params 失败: {exc}")
    cur_cols = set(current.columns) if current is not None else set()
    hist_cols = set(history.columns) if history is not None else set()
    # matrix_native 从 current + history 建矩阵, 一部分因子是矩阵内现算的虚拟列
    if s.matrix_strategy is not None:
        from app.backtest.matrix import _MATRIX_COMPUTED_FEATURES

        have = cur_cols | hist_cols | set(_MATRIX_COMPUTED_FEATURES)
    else:
        have = cur_cols
    missing = sorted(need - have)
    print(f"策略需要的列 {len(need)} 个: {sorted(need)}")
    print(f"缺: {missing if missing else '无'}")
    if params.get("scoring"):
        print(f"params.scoring 因子: {sorted(params['scoring'])}")

    _section("实跑命中数")
    result = engine.run(strategy_id, context, pool=None, params=params, overrides=override or None)
    print(f"as_of={result.as_of}  命中={result.total}  用时={result.elapsed_ms:.0f}ms")
    finite = sum(1 for v in result.scores.values() if v == v and v not in (0.0,))
    print(f"有非零分数的标的: {finite} / {len(result.scores)}")
    if result.rows:
        print("前 5 行:", [{k: v for k, v in row.items() if k in ("symbol", "name", "score")} for row in result.rows[:5]])
    if result.scores:
        top = sorted(result.scores.items(), key=lambda kv: kv[1], reverse=True)[:5]
        print("分数前 5:", top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
