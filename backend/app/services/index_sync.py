"""指数 / ETF 数据同步服务。

标的维表与日K都跟随日K数据源: 生效源实现 get_instruments / 提供 index·etf
日K 时直接用它; 源不服务该资产类型时回退默认源 (内置 easytdx)。
"""
from __future__ import annotations

import logging
import gc
from collections.abc import Callable
from datetime import datetime, timedelta

import polars as pl

from app.indicators.pipeline import compute_enriched
from app.services import kline_sync, preferences
from app.data_providers.capabilities import CapabilitySet
from app.data_providers.repository import KlineRepository

logger = logging.getLogger(__name__)

def _fetch_instruments_by_type(instrument_type: str, asset_type_label: str) -> pl.DataFrame:
    """拉取指定类型的标的列表 (指数 / ETF)。

    维表跟随日K数据源 (生效源 → 默认源), 源不提供维表时本轮为空。

    instrument_type: 'index' / 'etf'
    asset_type_label: 写入 instruments 表的 asset_type 标记('index' / 'etf')
    """
    from app.services import instrument_sync

    provider_rows = instrument_sync.fetch_instruments_via_provider(instrument_type) or []
    rows = [
        {"symbol": str(r.get("symbol")), "name": r.get("name") or str(r.get("symbol"))}
        for r in provider_rows
        if r.get("symbol")
    ]

    if not rows:
        return pl.DataFrame()

    return (
        pl.DataFrame(rows)
        .with_columns([
            pl.col("symbol").str.split(".").list.first().alias("code"),
            pl.lit(asset_type_label).alias("asset_type"),
        ])
        .unique(subset=["symbol"], keep="last")
        .sort("symbol")
    )


def sync_index_instruments(
    repo: KlineRepository,
    pull_index: bool = True,
    pull_etf: bool = True,
) -> int:
    """同步指数 / ETF 标的维表,返回标的总数。

    新版物理分开保存: 指数写 instruments_index, ETF 写 instruments_etf。
    读取层仍兼容旧版 instruments_index 中 asset_type='etf' 的历史数据。
    """
    index_parts: list[pl.DataFrame] = []
    etf_parts: list[pl.DataFrame] = []

    # 按开关分别拉 index / etf
    if pull_index:
        index_df = _fetch_instruments_by_type("index", "index")
        if not index_df.is_empty():
            index_parts.append(index_df)
    if pull_etf:
        etf_df = _fetch_instruments_by_type("etf", "etf")
        if not etf_df.is_empty():
            etf_parts.append(etf_df)

    total = 0
    if index_parts:
        index_inst = pl.concat(index_parts, how="diagonal_relaxed").unique(subset=["symbol"], keep="last").sort("symbol")
        if not index_inst.is_empty():
            repo.save_index_instruments(index_inst)
            total += index_inst.height
    if etf_parts:
        etf_inst = pl.concat(etf_parts, how="diagonal_relaxed").unique(subset=["symbol"], keep="last").sort("symbol")
        if not etf_inst.is_empty():
            repo.save_etf_instruments(etf_inst)
            total += etf_inst.height

    if total == 0:
        logger.warning("指数/ETF 标的列表为空(pull_index=%s, pull_etf=%s)", pull_index, pull_etf)
        return 0
    repo.refresh_index_views()
    logger.info("指数/ETF 标的同步完成: %d 只", total)
    return total


def sync_etf_instruments(repo: KlineRepository) -> int:
    """单独同步 ETF 标的维表(返回 ETF 数量)。"""
    etf_df = _fetch_instruments_by_type("etf", "etf")
    if etf_df.is_empty():
        return 0
    repo.save_etf_instruments(etf_df)
    repo.refresh_index_views()
    return etf_df.height


def _iter_custom_daily_frames(provider, symbols, asset_type, start_time, end_time, on_chunk_done):
    """自定义源日K帧迭代器: iter_daily 流式优先, 仅实现 get_daily 的源包装为单帧。

    asset_type 按内部契约传 "index"/"etf"; 只服务股票的源 (fuyao/amber
    对 asset_type≠stock 一律空手而归) 由此自然表达「不支持」, 由调用方回退。"""
    yield from kline_sync.iter_custom_daily_frames(
        provider, symbols, asset_type=asset_type, start_time=start_time,
        end_time=end_time, on_chunk_done=on_chunk_done,
    )


def _sync_custom_daily(
    repo: KlineRepository,
    symbols: list[str],
    *,
    asset_type: str,
    asset_label: str,
    start_time: datetime,
    end_time: datetime,
    on_chunk_done: Callable[[int, int], None] | None,
    persist_frame: Callable[[pl.DataFrame], int],
) -> int:
    """指数/ETF 日K同步: 生效日K源优先, 源不服务该资产类型时回退默认源。

    取数链 = [生效日K源, 默认日K源 (内置 easytdx)]。只服务股票的源 (如 fuyao)
    对 index/etf 一律空手而归, 由链上下一跳接管 —— 插件契约本就如此。

    返回 -1 表示「本轮没拿到任何数据」, 调用方按空结果处理。
    persist_frame 接收一帧原始日K并完成 append_*_daily + enriched 落盘,
    返回行数 —— 指数/ETF 各自复用同一套落盘逻辑, 不另起一套。
    """
    from app.data_providers import custom as custom_sources
    from app.data_providers.capabilities import default_provider_for

    chain: list[tuple[str, object]] = []
    for name in (preferences.get_daily_data_provider(), default_provider_for("daily")):
        if not name or any(n == name for n, _ in chain):
            continue
        if custom_sources.provider_has_dataset(name, "daily"):
            chain.append((name, custom_sources.get_provider(name)))
    if not chain:
        return -1

    for provider_name, provider in chain:
        total_rows = 0
        for raw in _iter_custom_daily_frames(
            provider, symbols, asset_type, start_time, end_time, on_chunk_done,
        ):
            if raw is None or raw.is_empty():
                continue
            total_rows += persist_frame(raw)
            logger.info("%s daily synced (%s): +%d rows", asset_label, provider_name, raw.height)
            del raw
            gc.collect()
        if total_rows > 0:
            repo.refresh_index_views()
            return total_rows
        logger.warning(
            "源 %s 未返回%s日K数据 (源仅服务股票时属预期), 回退下一源",
            provider_name, asset_label,
        )
    return -1


def sync_and_persist_index_daily(
    repo: KlineRepository,
    capset: CapabilitySet,
    count: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    symbols_override: list[str] | None = None,
    on_chunk_done: Callable[[int, int], None] | None = None,
) -> int:
    """同步指数/ETF 日K到独立 parquet,并计算 enriched。

    symbols_override 非空时,只拉这些代码(跳过 instruments 表),用于自定义范围。
    否则取 index_instruments 表全量(指数+ETF 合并存储)。
    on_chunk_done(current, total) 每个批次完成后回调。
    """
    if symbols_override:
        symbols = sorted(set(s for s in symbols_override if s))
        if not symbols:
            return 0
    else:
        instruments = repo.get_index_instruments()
        if instruments.is_empty():
            sync_index_instruments(repo, pull_index=True, pull_etf=False)
            instruments = repo.get_index_instruments()
        if not instruments.is_empty() and "asset_type" in instruments.columns:
            instruments = instruments.filter(pl.col("asset_type") != "etf")
        if instruments.is_empty() or "symbol" not in instruments.columns:
            return 0
        symbols = sorted(set(instruments["symbol"].to_list()))

    end_time = end_date or datetime.now()
    start_time = start_date or (end_time - timedelta(days=365))

    def _persist_index_frame(raw: pl.DataFrame) -> int:
        repo.append_index_daily(raw)
        enriched = compute_enriched(raw, factors=None, instruments=None)
        repo.append_index_enriched(enriched)
        del enriched
        return raw.height

    # 指数/ETF 日K走独立路由 (生效源 → 默认源); 个股的 sync_daily_batch 固定
    # asset_type=stock, 不能复用。
    rows = _sync_custom_daily(
        repo, symbols,
        asset_type="index", asset_label="指数",
        start_time=start_time, end_time=end_time,
        on_chunk_done=on_chunk_done,
        persist_frame=_persist_index_frame,
    )
    if rows < 0:
        kline_sync._log_cap_unavailable("daily", "指数/ETF 日K同步")
        return 0
    return rows


def _load_etf_factors(repo: KlineRepository) -> pl.DataFrame:
    factor_path = repo.store.data_dir / "adj_factor_etf" / "all.parquet"
    if not factor_path.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(factor_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("ETF 复权因子读取失败: %s", e)
        return pl.DataFrame()


def etf_adj_factor_window_start(adj_path, history_start: datetime) -> datetime:
    """ETF 除权拉取窗口起点。

    本地还没有因子文件时与日K历史对齐 (调用方传入 today-365 或最早分区日),
    不要只拉最近 30 天: 拆分落在窗口外则全年日K都不复权。已有文件则从最新
    事件日续拉。
    """
    if not adj_path.exists():
        return history_start
    try:
        max_date = pl.scan_parquet(adj_path).select(pl.col("trade_date").max()).collect().item()
    except Exception as e:
        logger.warning("读取 ETF 除权因子最新日期失败, 回退日K历史起点: %s", e)
        return history_start
    if max_date is None:
        return history_start
    if isinstance(max_date, str):
        stored = datetime.combine(datetime.fromisoformat(max_date).date(), datetime.min.time())
    elif isinstance(max_date, datetime):
        stored = datetime.combine(max_date.date(), datetime.min.time())
    else:
        stored = datetime.combine(max_date, datetime.min.time())
    return stored


def _load_local_etf_daily(repo: KlineRepository, symbols: list[str]) -> pl.DataFrame:
    """读本批 symbol 的完整本地 ETF 日K, 供前复权改写历史价。"""
    daily_dir = repo.store.data_dir / "kline_etf_daily"
    if not daily_dir.exists() or not any(daily_dir.glob("date=*")):
        return pl.DataFrame()
    from app.parquet import dataset_glob, scan_daily_parquet
    try:
        return (
            scan_daily_parquet(dataset_glob(daily_dir))
            .filter(pl.col("symbol").is_in(symbols))
            .collect()
        )
    except Exception as e:
        logger.warning("读取本地 ETF 日K失败: %s", e)
        return pl.DataFrame()


def sync_etf_adj_factor(
    symbols: list[str],
    repo: KlineRepository,
    capset: CapabilitySet,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    on_chunk_done=None,
) -> tuple[int, list[str]]:
    """同步 ETF 复权因子；失败由调用方降级为 warning。"""
    return kline_sync.sync_adj_factor(
        symbols,
        repo,
        capset,
        start_time=start_time,
        end_time=end_time,
        on_chunk_done=on_chunk_done,
        asset_type="etf",
    )


def sync_and_persist_etf_daily(
    repo: KlineRepository,
    capset: CapabilitySet,
    count: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    symbols_override: list[str] | None = None,
    on_chunk_done: Callable[[int, int], None] | None = None,
) -> int:
    """同步 ETF 日K到独立 kline_etf_* parquet,并计算 ETF enriched。
    on_chunk_done(current, total) 每个批次完成后回调。
    """
    if symbols_override:
        symbols = sorted(set(s for s in symbols_override if s))
    else:
        instruments = repo.get_etf_instruments()
        if instruments.is_empty():
            sync_etf_instruments(repo)
            instruments = repo.get_etf_instruments()
        if instruments.is_empty() or "symbol" not in instruments.columns:
            return 0
        symbols = sorted(set(instruments["symbol"].to_list()))
    if not symbols:
        return 0

    end_time = end_date or datetime.now()
    start_time = start_date or (end_time - timedelta(days=365))

    factors = _load_etf_factors(repo)

    def _persist_etf_frame(raw: pl.DataFrame) -> int:
        frame_symbols = raw["symbol"].unique().to_list()
        repo.append_etf_daily(raw)
        batch_factors = (
            factors.filter(pl.col("symbol").is_in(frame_symbols))
            if not factors.is_empty() else factors
        )
        # 前复权 ratio = cum/total, 新除权事件会改写全部历史价。只用本次拉取
        # 窗口算 enriched 时, 拆分日前的分区停在未复权价, 日K 留下跳空。
        local = _load_local_etf_daily(repo, frame_symbols)
        hist = local if not local.is_empty() else raw
        # ETF 使用复权和通用技术指标; 不传 instruments, 避免套用 A股涨跌停/连板逻辑。
        enriched = compute_enriched(hist, factors=batch_factors, instruments=None)
        repo.append_etf_enriched(enriched)
        del enriched, local, hist
        return raw.height

    # ETF 日K走独立路由, 语义与上方指数分支一致
    rows = _sync_custom_daily(
        repo, symbols,
        asset_type="etf", asset_label="ETF",
        start_time=start_time, end_time=end_time,
        on_chunk_done=on_chunk_done,
        persist_frame=_persist_etf_frame,
    )
    if rows < 0:
        kline_sync._log_cap_unavailable("daily", "ETF 日K同步")
        return 0
    return rows
