"""Lightweight mining date availability checks shared by API and workers."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.backtest.mining import (
    MAX_MINING_FACTORS,
    MiningBudget,
    inner_fold_count,
    nested_fold_count,
    required_outer_folds,
    required_trading_bars,
    trim_fold_count,
    validation_config_for_profile,
)
from app.data_providers.repository import enriched_dirname
from app.market_time import cn_today


@dataclass(frozen=True)
class MiningAvailability:
    asset_type: str
    budget_profile: str
    trading_bars: int
    required_bars: int
    outer_folds: int
    required_outer_folds: int
    eligible: bool
    available_start: date | None
    available_end: date | None
    effective_start: date | None
    effective_end: date | None
    suggested_start: date | None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in asdict(self).items()
        }


def enriched_partition_dates(
    data_dir: Path,
    asset_type: str,
    start: date | None = None,
    end: date | None = None,
) -> list[date]:
    root = data_dir / enriched_dirname(asset_type)
    values: set[date] = set()
    for partition in root.glob("date=*"):
        try:
            value = date.fromisoformat(partition.name.removeprefix("date="))
        except ValueError:
            continue
        if start is not None and value < start:
            continue
        if end is not None and value > end:
            continue
        if (partition / "part.parquet").is_file():
            values.add(value)
    return sorted(values)


def mining_data_end(
    data_dir: Path,
    asset_type: str,
    end: date | None = None,
    *,
    dates: Sequence[date] | None = None,
    today: date | None = None,
) -> date | None:
    """挖掘可用的最后一天: 严格早于今天的最后一个分区, 显式 end 取更早者。

    实时行情每轮覆写当天分区并换世代, 回测/挖掘读到当天数据必然撞世代漂移;
    固定到前一交易日 (及更早) 才能拿到稳定快照。
    """
    all_dates = list(dates) if dates is not None else enriched_partition_dates(
        data_dir, asset_type,
    )
    if not all_dates:
        return end
    cutoff = today or cn_today()
    earlier = [value for value in all_dates if value < cutoff]
    # 只有今天一个分区 (新装/首日同步) 时无从退让, 仍按今天算。
    cap = earlier[-1] if earlier else all_dates[-1]
    return cap if end is None else min(end, cap)


def mining_availability(
    data_dir: Path,
    *,
    asset_type: str,
    budget_profile: str,
    start: date | None = None,
    end: date | None = None,
) -> MiningAvailability:
    if asset_type not in {"stock", "etf"}:
        raise ValueError(f"unsupported mining asset type: {asset_type}")
    if start is not None and end is not None and start > end:
        raise ValueError("mining start must not be after end")

    config = validation_config_for_profile(budget_profile)
    required_folds = required_outer_folds(budget_profile)
    required_bars = required_trading_bars(config, required_folds)
    all_dates = enriched_partition_dates(data_dir, asset_type)
    end = mining_data_end(data_dir, asset_type, end, dates=all_dates)
    scoped = [
        value
        for value in all_dates
        if (start is None or value >= start) and (end is None or value <= end)
    ]
    dates_through_end = [
        value for value in all_dates if end is None or value <= end
    ]
    suggested_start = (
        dates_through_end[-required_bars]
        if len(dates_through_end) >= required_bars
        else None
    )
    trading_bars = len(scoped)
    budget = getattr(MiningBudget, budget_profile)()
    # 与运行口径一致: 历史切得出的折数还要过代理预算这一关, 只保留最近的若干折。
    # 这里不知道用户最终选哪些因子, 按目录上限估 —— 只会偏保守, 不会多报。
    outer_folds = trim_fold_count(
        nested_fold_count(trading_bars, config),
        profile=budget_profile,
        max_proxy_trials=budget.max_proxy_trials,
        beam_width=budget.beam_width,
        phases_per_fold=inner_fold_count(config) + 1,
        factor_count=MAX_MINING_FACTORS,
    )
    return MiningAvailability(
        asset_type=asset_type,
        budget_profile=budget_profile,
        trading_bars=trading_bars,
        required_bars=required_bars,
        outer_folds=outer_folds,
        required_outer_folds=required_folds,
        eligible=trading_bars >= required_bars,
        available_start=all_dates[0] if all_dates else None,
        available_end=all_dates[-1] if all_dates else None,
        effective_start=scoped[0] if scoped else None,
        effective_end=scoped[-1] if scoped else None,
        suggested_start=suggested_start,
    )


def require_mining_availability(
    data_dir: Path,
    *,
    asset_type: str,
    budget_profile: str,
    start: date | None = None,
    end: date | None = None,
) -> MiningAvailability:
    availability = mining_availability(
        data_dir,
        asset_type=asset_type,
        budget_profile=budget_profile,
        start=start,
        end=end,
    )
    if availability.eligible:
        return availability

    if availability.effective_start is None:
        effective_range = "contains no enriched data"
    else:
        effective_range = (
            f"{availability.effective_start.isoformat()} to "
            f"{availability.effective_end.isoformat()}"
        )
    fold_label = "outer fold" if availability.required_outer_folds == 1 else "outer folds"
    raise ValueError(
        f"{budget_profile} mining requires at least {availability.required_bars} "
        f"enriched trading bars for {availability.required_outer_folds} {fold_label}; "
        f"effective range {effective_range} has {availability.trading_bars}"
    )
