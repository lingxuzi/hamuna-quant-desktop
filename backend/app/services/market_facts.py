"""近 N 天市场事实蒸馏: 大盘 / 板块 / 资金流三个维度。

只做「市场长什么样」的聚合描述, 不产出任何策略结论; 有效性判定留给长窗口
挖掘与回测 (见 docs 与策略生成入口)。任一显式选中的维度缺数据时抛
MarketFactsUnavailableError (fail-closed), 由 API 层转成带指路文案的 400。
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

import polars as pl

from app.services.ext_data import ExtConfigStore

DIMENSIONS = ("market", "sector", "money_flow")
DEFAULT_WINDOW_DAYS = 120
# 板块股票池上限: 与挖掘请求 symbols 校验 (≤10000) 对齐, 远小于该值以避免池内噪声
SECTOR_POOL_LIMIT = 3000
_TOP_SECTORS = 8
_RECENT_DETAIL_DAYS = 10


class MarketFactsUnavailableError(ValueError):
    """显式选中的维度没有可用数据; 消息直接展示给用户 (含指路)。"""


def build_market_facts(
    data_dir,
    repo,
    *,
    dimensions: Iterable[str],
    asset_type: str = "stock",
    window_days: int = DEFAULT_WINDOW_DAYS,
    as_of: date | None = None,
) -> dict:
    """按维度构建事实; 返回 {window, as_of, asset_type, market?, sector?, money_flow?}。"""
    picked = list(dict.fromkeys(dimensions))
    unknown = sorted(set(picked) - set(DIMENSIONS))
    if unknown:
        raise ValueError("不支持的参考维度: " + ", ".join(unknown))
    if not picked:
        raise ValueError("请至少选择一个参考维度")
    if asset_type not in {"stock", "etf"}:
        raise ValueError(f"不支持的标的类型: {asset_type}")
    if not 20 <= window_days <= 500:
        raise ValueError("观察窗口需在 20 至 500 天之间")

    end = as_of or date.today()
    start = end - timedelta(days=window_days)
    builders = {
        "market": lambda: _market(data_dir, start, end),
        "sector": lambda: _sector(data_dir, repo, asset_type, start, end),
        "money_flow": lambda: _money_flow(data_dir, start, end),
    }
    facts: dict = {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": window_days},
        "as_of": end.isoformat(),
        "asset_type": asset_type,
        "dimensions": picked,
    }
    for name in picked:
        facts[name] = builders[name]()
    return facts


def _in_window(df: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    """按 date 列裁剪 (兼容 date 类型与字符串类型两种落盘 schema)。"""
    col = pl.col("date")
    if df.schema["date"] in (pl.Date, pl.Datetime):
        cond = (col >= pl.lit(start).cast(df.schema["date"])) & (
            col <= pl.lit(end).cast(df.schema["date"])
        )
    else:
        cond = (col >= start.isoformat()) & (col <= end.isoformat())
    return df.filter(cond)


def _market(data_dir, start: date, end: date) -> dict:
    from app.services.regime_builder import load_regime_history

    df = load_regime_history(data_dir)
    if df.is_empty() or "date" not in df.columns:
        raise MarketFactsUnavailableError("大盘状态序列为空, 请先在「市场状态」页生成状态历史")
    window = _in_window(df, start, end).sort("date")
    if window.is_empty():
        raise MarketFactsUnavailableError(
            f"近 {(end - start).days} 天内没有大盘状态数据, 请先在「市场状态」页重新计算"
        )

    def _mean(col: str):
        return _round(window[col].mean()) if col in window.columns else None

    def _last(col: str):
        return _round(window[col].tail(1).item()) if col in window.columns else None

    states = window.group_by("state").len().sort("len", descending=True).rows()
    phases = (
        window.group_by("phase").len().sort("len", descending=True).rows()
        if "phase" in window.columns
        else []
    )
    # up_ratio 是 up/down 的比值 (regime_builder 口径), 直接 ×100 会得到 155% 这种
    # 不可能值。这里另给一个真正的上涨占比 (up/(up+down), 0-1), 前端一律用它展示。
    if {"up_count", "down_count"} <= set(window.columns):
        total_ud = (window["up_count"] + window["down_count"]).cast(pl.Float64)
        share = pl.when(total_ud > 0).then(window["up_count"] / total_ud).otherwise(None)
        window = window.with_columns(share.alias("up_share"))
    detail_cols = [
        c for c in ("date", "state", "score", "phase", "up_ratio", "up_share", "index_pct")
        if c in window.columns
    ]
    return {
        "bars": window.height,
        "states": [{"state": s, "days": n} for s, n in states],
        "phases": [{"phase": p, "days": n} for p, n in phases],
        "score": {"mean": _mean("score"), "last": _last("score")},
        "up_ratio": {"mean": _mean("up_ratio"), "last": _last("up_ratio")},
        "up_share": {"mean": _mean("up_share"), "last": _last("up_share")},
        "avg_pct": {"mean": _mean("avg_pct"), "last": _last("avg_pct")},
        "index_pct": {"mean": _mean("index_pct"), "last": _last("index_pct")},
        "above_ma20_pct": {"mean": _mean("above_ma20_pct"), "last": _last("above_ma20_pct")},
        "turnover": {"mean": _mean("avg_turnover"), "last": _last("avg_turnover")},
        "phase_last": (
            window["phase"].tail(1).item() if "phase" in window.columns else None
        ),
        "recent": window.tail(_RECENT_DETAIL_DAYS).select(detail_cols).to_dicts(),
    }


def _sector(data_dir, repo, asset_type, start: date, end: date) -> dict:
    from app.services.market_mainline import load_mainline_history

    # 主线历史 concept 与 industry 各持久化一份; 概念维度与「板块」语义最接近
    df = load_mainline_history(data_dir, "concept")
    if df.is_empty():
        raise MarketFactsUnavailableError(
            "板块主线历史为空, 请先在「板块轮动」页运行一次主线计算"
        )
    window = _in_window(df, start, end)
    if window.is_empty():
        raise MarketFactsUnavailableError(
            f"近 {(end - start).days} 天内没有板块主线数据, 请先在「板块轮动」页重新计算"
        )
    grouped = (
        window.group_by("member")
        .agg(
            pl.len().alias("days"),
            pl.col("score").mean().alias("avg_score"),
            pl.col("rank").min().alias("best_rank"),
            pl.col("limit_up_count").sum().alias("limit_up_sum"),
        )
        .sort(["days", "avg_score"], descending=True)
    )
    half = (end - start) / 2
    mid = start + half
    first = window.filter(pl.col("date") < mid)
    second = window.filter(pl.col("date") >= mid)

    def _avg_score(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.group_by("member").agg(pl.col("score").mean().alias("s")).rename({"s": "_s"})

    trend = _avg_score(second).join(_avg_score(first), on="member", how="full", coalesce=True)
    trend = trend.with_columns(
        (pl.col("_s").fill_null(0.0) - pl.col("_s_right").fill_null(0.0)).alias("delta")
    )
    rising = _briefs(trend.sort("delta", descending=True).head(5))
    fading = _briefs(trend.sort("delta").head(5))
    momentum = dict(trend.select("member", "delta").iter_rows())

    sectors = [
        {
            "member": row["member"],
            "days": int(row["days"]),
            "avg_score": _round(row["avg_score"]),
            "best_rank": int(row["best_rank"]),
            "limit_up_sum": int(row["limit_up_sum"]),
            "momentum": _round(momentum.get(row["member"])),
        }
        for row in grouped.head(_TOP_SECTORS).iter_rows(named=True)
    ]
    leaders = grouped.select("member").head(_TOP_SECTORS * 2)["member"].to_list()
    pool = _sector_symbol_pool(repo, leaders, asset_type)
    return {
        "sectors": sectors,
        "rising": rising,
        "fading": fading,
        "pool": {
            "members": leaders,
            "symbols": pool,
            "size": len(pool),
            "note": "成分按当前快照映射, 回看历史存在归属漂移"
            if pool
            else "未找到板块成分映射",
        },
    }


def _briefs(trend: pl.DataFrame) -> list[dict]:
    return [
        {"member": row["member"], "momentum": _round(row["delta"])}
        for row in trend.iter_rows(named=True)
    ]


def _sector_symbol_pool(repo, members: list[str], asset_type: str) -> list[str]:
    """强势板块成分股 (与主线/挖掘同一口径的 symbol 形式); 空映射时返回空表。

    复用 rps_rotation 的已展开映射表 (600s 进程内缓存), 不另解析扩展表成分。
    """
    from app.services.rps_rotation import _load_concept_map_df

    map_df, _ = _load_concept_map_df(repo, "concept")
    if map_df.is_empty():
        return []
    # 映射表同时含 "600000" 与 "600000.SH" 两种写法; 只保留带交易所后缀的规范形式
    symbols = [
        s
        for s in map_df.filter(pl.col("concept").is_in(members))["_sym_up"].unique().sort()
        if "." in s
    ]
    if asset_type == "etf":
        symbols = [s for s in symbols if s.startswith(("51", "56", "58", "159"))]
    return symbols[:SECTOR_POOL_LIMIT]


def _money_flow(data_dir, start: date, end: date) -> dict:
    configs = [
        cfg for cfg in ExtConfigStore(data_dir).load_all()
        if cfg.mode == "timeseries" and _numeric_fields(cfg)
    ]
    if not configs:
        raise MarketFactsUnavailableError(
            "还没有可用的资金流数据: 请先到「扩展数据」建一张时序型扩表"
            "(按日采集) 并配置数值字段 (如主力净流入、成交额), 再回补近 120 天历史"
        )
    from app.factors.ext_factors import _timeseries_frame, ext_column_name

    tables = []
    all_values: list[float] = []
    for cfg in configs:
        fields = _numeric_fields(cfg)
        frame = _timeseries_frame(data_dir, cfg, fields)
        if frame.is_empty():
            continue
        window = frame.filter(
            (pl.col("_ext_date") >= start.isoformat())
            & (pl.col("_ext_date") <= end.isoformat())
        )
        if window.is_empty():
            continue
        for field in fields:
            col = ext_column_name(cfg.id, field.name)
            series = window[col].drop_nulls() if col in window.columns else pl.Series([])
            if series.is_empty():
                continue
            recent = series.tail(min(20, series.len()))
            earlier = series.head(max(0, series.len() - recent.len()))
            values = series.to_list()
            all_values.extend(values)
            tables.append({
                "table": cfg.label,
                "field": field.label or field.name,
                "points": series.len(),
                "mean": _round(series.mean()),
                "last": _round(series.tail(1).item()),
                "recent_vs_earlier": (
                    _round(recent.mean() - earlier.mean()) if not earlier.is_empty() else None
                ),
            })
    if not tables:
        raise MarketFactsUnavailableError(
            "扩展表里配了数值字段, 但近 120 天没有时序数据: 请到「扩展数据」补采历史"
        )
    return {
        "tables": tables,
        "coverage": {"points": len(all_values), "tables": len({t["table"] for t in tables})},
    }


def _numeric_fields(config):
    from app.factors.ext_factors import _NUMERIC_DTYPES

    return [f for f in config.fields if f.dtype in _NUMERIC_DTYPES]


def _round(value):
    return None if value is None else round(float(value), 4)
