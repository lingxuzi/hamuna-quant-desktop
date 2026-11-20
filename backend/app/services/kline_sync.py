"""日 K 同步服务(§7.7 Step 1)。

调度器在 capability 允许下,把符号集合的日 K 批量同步到本地 Parquet。
策略:
  - 日 K 仅使用 `kline.daily.batch`
  - 除权因子仅使用 `adj_factor`
"""
from __future__ import annotations

import contextlib
import inspect
import logging
import shutil
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from app.data_providers.base import AssetType, ProviderRequestError
from app.data_providers.capabilities import Cap, CapabilitySet
from app.data_providers.ex_factor import recompute_ex_factor
from app.data_providers.rate_limits import chunked, resolve_limit
from app.data_providers.repository import KlineRepository, replace_with_retry
from app.indicators.pipeline import filter_halt_days
from app.market_time import CN_TZ, cn_now, cn_today
from app.services import minute_adjust, preferences
from app.services.shared_work import SharedWork, WorkBusyError
from app.symbols import exchange_of_code

logger = logging.getLogger(__name__)

# 自定义分钟源流式落盘的块大小(标的数): 每块拉完立即 on_segment 写盘,
# 长任务中断只丢当前块; 与 cnfree 内部 _HIST_SYMBOL_BATCH 同量级。
_CUSTOM_MINUTE_CHUNK = 50
_single_minute_work = SharedWork("minute.single", max_pending=128, max_entries=512,
                               max_bytes=16 * 1024 * 1024, size_of=lambda frame: frame.estimated_size())


def _log_cap_unavailable(cap_id: str, action: str) -> None:
    """门禁未通过时记一条带生效源的日志。

    原实现是静默 return —— 切换数据源后「什么都没发生」且无迹可循。
    带源标签后可一眼看出是路由到不可用源, 还是该能力根本没有生效源。
    """
    try:
        from app.data_providers.capabilities import capability_gate
        _, label = capability_gate(cap_id)
    except Exception:  # noqa: BLE001 — 仅日志, 失败不应影响主流程
        label = cap_id
    logger.info("%s 跳过: %s 不可用", action, label)


# ── 数据集取数源解析 ────────────────────────────────────────────────────
# 生效源 (偏好路由) 优先; 生效源没提供该数据集时回退内置默认源 (easytdx)。
# 回退源取自能力注册表, 不在这里硬编码源名 —— 硬编码会让注册表改了这里不跟。
def _dataset_provider(dataset: str) -> tuple[object | None, str]:
    """数据集生效源 → (provider 实例, 源名); 拿不到返回 (None, 生效源名)。"""
    from app.data_providers import custom as custom_sources
    return custom_sources.resolve_provider(preferences.get_data_provider(dataset), dataset)


def _provider_chain(dataset: str) -> list[tuple[str, object]]:
    """数据集取数源链: 生效源优先, 内置默认源 (easytdx) 兜底。

    生效源声明了该数据集但对某类资产空手而归时, 按契约走到链上下一跳;
    生效源就是内置源时链上只有一跳, 拿不到就是真没有。
    """
    from app.data_providers import custom as custom_sources
    from app.data_providers.capabilities import default_provider_for

    chain: list[tuple[str, object]] = []
    for name in (preferences.get_data_provider(dataset), default_provider_for(dataset)):
        if not name or any(n == name for n, _ in chain):
            continue
        if custom_sources.provider_has_dataset(name, dataset):
            chain.append((name, custom_sources.get_provider(name)))
    return chain


def _atomic_write_parquet(df: pl.DataFrame, out) -> None:
    """先写临时文件再原子替换, 避免进程中断留下损坏的 parquet。

    与 repository._atomic_write_parquet 同语义。adj_factor 的 all.parquet 是全市场
    单文件、每次「读→concat→原地写」, 直接 write_parquet(out) 在进程被 kill
    (dev.sh 清端口用 kill -9)、reap 超时或断电时会留下半截文件, 之后复权视图
    scan_parquet 整条链路报错、enriched 全市场重算不出。临时文件后缀 .tmp 不匹配
    *.parquet glob, 不会被扫描误读。Windows 下目标正被并发读取时由
    replace_with_retry 短退避穿过。
    """
    # 唯一后缀: 两个写入者共用固定名 tmp 并发写同一文件会交错损坏 (被 rename
    # 定版后分区报废)。崩溃残留的孤儿 tmp 不匹配 *.parquet glob, 读侧无影响。
    tmp = out.with_name(f"{out.name}.{uuid.uuid4().hex[:8]}.tmp")
    df.write_parquet(tmp)
    replace_with_retry(tmp, out)


# 标准列(无论 SDK 返回什么形状,我们把它规范成这套)
CANONICAL_DAILY_COLS = [
    "symbol", "date", "open", "high", "low", "close", "volume", "amount",
]


def _normalize_daily(df_in, default_symbol: str | None = None) -> pl.DataFrame:
    """把 SDK 返回的 pandas/任意 DataFrame 规范成 canonical 列。"""
    if df_in is None or len(df_in) == 0:
        return pl.DataFrame()

    if not isinstance(df_in, pl.DataFrame):
        df = pl.from_pandas(df_in.reset_index() if hasattr(df_in, "reset_index") else df_in)
    else:
        df = df_in

    # 兼容字段名差异
    rename_map = {
        "ts_code": "symbol",
        "trade_date": "date",
        "vol": "volume",
        "amt": "amount",
        "datetime": "date",
    }
    df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})

    if "symbol" not in df.columns and default_symbol is not None:
        df = df.with_columns(pl.lit(default_symbol).alias("symbol"))

    # 类型规范
    if "date" in df.columns and df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date, strict=False))

    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
    for col in ("volume", "amount"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    # 过滤停牌日 (open/high 为 0; close 可能被填充为前收盘价, 不能用全零判断)
    df = filter_halt_days(df)

    # 只保留 canonical 列
    keep = [c for c in CANONICAL_DAILY_COLS if c in df.columns]
    return df.select(keep)


def _instruments_symbol_index(data_dir) -> dict[str, list[str]] | None:
    """裸代码 → instruments 维表完整符号列表; 维表缺失/不可读返回 None。"""
    path = data_dir / "instruments" / "instruments.parquet"
    if not path.exists():
        return None
    try:
        symbols = pl.read_parquet(path, columns=["symbol"])["symbol"].drop_nulls().to_list()
    except Exception as e:
        logger.warning("instruments 维表读取失败, 裸符号无法补全后缀: %s", e)
        return None
    index: dict[str, list[str]] = {}
    for s in symbols:
        code, _, _suffix = str(s).partition(".")
        if code:
            index.setdefault(code, []).append(str(s))
    return index


def _live_symbol_index(codes: list[str]) -> dict[str, str] | None:
    """向生效日K源 (或默认源) 要全市场代码表 → {裸代码: 完整符号}。

    内置 easytdx 走标准协议证券列表 (与 MAC 行情通道独立), 权威且含北交所;
    比本地维表全 (维表是 MAC 快照, 缺新上市代码)。源不提供该能力 / 调用
    失败 → 返回 None, 调用方退回代码前缀推断。
    """
    from app.data_providers import custom as custom_sources
    from app.data_providers.capabilities import default_provider_for

    for name in (preferences.get_data_provider("daily"), default_provider_for("daily")):
        if not name or not custom_sources.is_custom_provider(name):
            continue
        try:
            provider = custom_sources.get_provider(name)
        except ValueError:
            continue
        resolver = getattr(provider, "resolve_symbols", None)
        if not callable(resolver):
            continue
        try:
            return resolver(codes) or {}
        except Exception as e:  # noqa: BLE001 — 补全失败不该中断同步
            logger.warning("日K同步: 数据源 %s 全市场代码表获取失败: %s", name, e)
            return None
    return None


def _normalize_bare_symbols(
    symbols: list[str], data_dir,
) -> tuple[list[str], dict[str, str], list[str]]:
    """裸符号(无交易所后缀, 如 600000)补全为 600000.SH (#302)。

    返回 (规范化后的去重请求列表, {裸符号: 补全后符号}, 被跳过的裸符号)。
    上游对裸符号返回 200 空 payload, 本地静默写 0 行 — 补全失败时显式跳过并告警,
    不再带着已知无效的符号发请求。全部带后缀时不读维表, 行为与开销同旧版;
    带后缀符号原样透传, 由上游照常处理。

    补全顺序: 维表唯一匹配(权威) → 全市场代码表(权威, 维表缺失或不含时) →
    代码前缀推断(代码表不可用时兜底) → 都判不出才跳过。维表缺失不该让
    整批裸符号写不进去。
    """
    bare = [s for s in symbols if "." not in s]
    if not bare:
        return symbols, {}, []

    index = _instruments_symbol_index(data_dir)
    resolved: dict[str, str] = {}
    skipped: list[str] = []
    pending: list[str] = []
    for s in bare:
        matches = (index or {}).get(s, [])
        if len(matches) == 1:
            resolved[s] = matches[0]
        elif matches:
            # 同代码多交易所 (000001.SZ 平安 / 000001.SH 上证) → 按前缀消歧
            suffix = exchange_of_code(s)
            inferred = f"{s}.{suffix}" if suffix else None
            resolved[s] = inferred if inferred in matches else matches[0]
        else:
            pending.append(s)

    # 维表缺失/不含该代码 → 向数据源要全市场代码表 (权威, 比维表全)
    live = _live_symbol_index(pending) if pending else None
    for s in pending:
        if live is not None:
            sym = live.get(s)
            if sym:
                resolved[s] = sym
            else:
                skipped.append(s)
            continue
        suffix = exchange_of_code(s)
        if suffix is None:
            skipped.append(s)
        else:
            resolved[s] = f"{s}.{suffix}"

    if index is None:
        logger.warning(
            "日K同步: instruments 维表不可用, %d 个裸符号改用全市场代码表补全后缀 (建议先同步标的维表)",
            len(resolved))
    if skipped:
        if live is not None:
            logger.warning(
                "日K同步: %d 个裸符号不在全市场代码表内(代码不存在), 已跳过: %s",
                len(skipped), skipped[:20])
        else:
            logger.warning(
                "日K同步: %d 个裸符号无法判定交易所(非 6 位 A 股代码), 已跳过: %s",
                len(skipped), skipped[:20])
    if resolved:
        logger.info("日K同步: 已补全 %d 个裸符号后缀 (样例: %s)",
                    len(resolved), list(resolved.items())[:5])

    out: list[str] = []
    seen: set[str] = set()
    skipped_set = set(skipped)
    for s in symbols:
        if s in skipped_set:
            continue  # 无法补全的裸符号不发请求 (上游必然 200 空 payload)
        full = resolved.get(s, s)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out, resolved, skipped


def sync_daily_batch(symbols: list[str],
                     count: int | None = None,
                     batch_size: int | None = None,
                     rpm: int | None = None,
                     start_time: datetime | None = None,
                     end_time: datetime | None = None,
                     on_chunk_done: Callable[[int, int], None] | None = None,
                     failed_out: list[str] | None = None) -> pl.DataFrame:
    """批量拉取多股日 K (走生效日K源的 provider 契约)。

    优先使用 start_time / end_time 区间; 仅传 count 时按条数回溯成自然日窗口
    交给源侧换算成它自己的请求根数。

    batch_size / rpm: 源侧自带节流, 这两个参数保留只为兼容调用方签名。
    failed_out: 可选出参。provider 逐标的失败时追加进该 list, 供上层判定
                「部分失败」而非静默当成功(某标的断网 → 本轮未更新, 保持旧数据)。
    """
    provider, _provider_name = _dataset_provider("daily")
    if provider is None:
        raise DailySourceUnavailableError("当前日K数据源不可用, 请检查数据源配置")
    if start_time is None and end_time is None and count:
        end_time = datetime.now()
        # 交易日 ≈ 自然日 × 5/7, 留一周冗余给停牌
        start_time = end_time - timedelta(days=int(count * 7 / 5) + 5)
    out: list[pl.DataFrame] = []
    failed_syms: list[str] = []
    kwargs: dict = dict(start_time=start_time, end_time=end_time, on_chunk_done=on_chunk_done)
    kwargs.update(_symbol_failure_sink(provider, failed_syms))
    for frame in iter_custom_daily_frames(
        provider, symbols, asset_type="stock", **kwargs,
    ):
        if not frame.is_empty():
            out.append(frame)
    if failed_out is not None and failed_syms:
        failed_out.extend(failed_syms)
    if not out:
        return pl.DataFrame()
    return pl.concat(out, how="diagonal_relaxed")


def _symbol_failure_sink(provider, failed_syms: list[str]) -> dict:
    """provider 声明了逐标的失败回调时, 返回 {"on_symbol_failed": cb}; 否则 {}。

    逐票失败通道是可选契约: 只有声明 on_symbol_failed 的源才收这个参数
    (不声明的源传了会 TypeError)。有通道时单票拉取失败与「窗口内无数据」
    分开记账 —— 后者走 zero_row_out, 前者上浮 stage_errors (#514)。
    """
    fn = getattr(provider, "iter_daily", None)
    if not callable(fn):
        return {}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if "on_symbol_failed" not in params:
        return {}

    def _on_failed(symbol: str, reason: str) -> None:
        failed_syms.append(symbol)
        logger.warning("日K同步: 标的 %s 拉取失败: %s", symbol, reason)

    return {"on_symbol_failed": _on_failed}


def iter_custom_daily_frames(provider, symbols, *, asset_type="stock", **kwargs):
    """复用流式/单帧插件契约; 旧股票插件可继续省略 asset_type 参数。"""
    if asset_type != "stock":
        kwargs["asset_type"] = asset_type
    stream = getattr(provider, "iter_daily", None)
    if callable(stream):
        yield from stream(symbols, **kwargs)
    else:
        frame = provider.get_daily(symbols, **kwargs)
        if not frame.is_empty():
            yield frame


class DailySourceUnavailableError(RuntimeError):
    """有效日K路由没有可用数据能力。"""


def iter_daily_frames(
    symbols: list[str], *,
    start_time: datetime, end_time: datetime, asset_type: str = "stock", on_chunk_done=None,
):
    """只取标准日K帧, 不写正式数据、不计算指标; 调用方决定校验和发布边界。

    日K可用性由取数源链判定 (生效源声明 daily 即可用), 不再读能力集。
    """
    if asset_type not in {"stock", "etf", "index"}:
        raise ValueError("不支持的日K资产类型")

    chain = _provider_chain("daily")
    if not chain:
        raise DailySourceUnavailableError("当前日K数据源不可用, 请检查数据源配置")
    for provider_name, provider in chain:
        logger.info("日K修补取数: provider=%s asset=%s symbols=%d start=%s end=%s",
                    provider_name, asset_type, len(symbols), start_time.isoformat(), end_time.isoformat())
        found = False
        for frame in iter_custom_daily_frames(
            provider, symbols, asset_type=asset_type,
            start_time=start_time, end_time=end_time, on_chunk_done=on_chunk_done,
        ):
            if not frame.is_empty():
                found = True
                yield frame
        # 非股票源沿用指数/ETF 原有的「明确不支持时回退」契约。
        if found or asset_type == "stock":
            return
        logger.warning("日K源 %s 未返回 %s 数据, 修补回退下一源", provider_name, asset_type)
    raise DailySourceUnavailableError(f"当前日K数据源未返回 {asset_type} 数据, 请检查数据源配置")


def sync_and_persist_daily_batch(
    symbols: list[str],
    repo: KlineRepository,
    capset: CapabilitySet,
    count: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    on_chunk_done: Callable[[int, int], None] | None = None,
    zero_row_out: list[str] | None = None,
    failed_out: list[str] | None = None,
) -> int:
    """批量同步日 K 并落到 Parquet。返回写入的行数。

    start_date/end_date: 外部传入的时间范围(由 pipeline 根据已有数据计算)。
    未传入时默认拉最近 1 年。
    zero_row_out: 可选出参。本轮实际入库 0 行的标的(含无法识别被跳过的裸符号、
                  上游 200 空 payload 的静默 0 行)按调用方原始写法追加进该 list,
                  供上层 fail-loud 展示, 不再"显示成功实则全空" (#302)。
    failed_out: 可选出参。provider 逐票拉取失败(超时/断连)的标的, 供上层把
                单票失败上浮为 stage_errors 而非静默当成功 (#514)。
                源未实现逐票失败通道时该 list 保持为空 (这些标的按 0 行回报)。
    """
    seen: set[str] = set()
    failed_syms: list[str] = []
    skipped_bare: list[str] = []

    def _finalize(written: int) -> int:
        if failed_out is not None and failed_syms:
            # 单票失败上浮给调用方 (daily_pipeline 汇入 stage_errors, #514)
            failed_out.extend(orig_by_full.get(s, s) for s in failed_syms)
        zero_full = [s for s in requested if s not in seen and s not in failed_syms]
        if zero_full or skipped_bare:
            zero = [orig_by_full.get(s, s) for s in zero_full] + skipped_bare
            logger.warning(
                "日K批量同步: %d/%d 标的本轮入库 0 行 (符号无法识别/停牌/窗口内无数据; 样例: %s)",
                len(zero), len(requested) + len(skipped_bare), zero[:10])
            if zero_row_out is not None:
                zero_row_out.extend(zero)
        return written

    original = list(symbols)
    symbols, resolved, skipped_bare = _normalize_bare_symbols(symbols, repo.store.data_dir)
    requested = symbols
    # 完整符号 → 调用方原始写法: 0 行回报用用户输入的形式, 裸符号也能对上
    orig_by_full: dict[str, str] = {}
    for orig in original:
        orig_by_full.setdefault(resolved.get(orig, orig), orig)

    if not symbols:
        return _finalize(0)

    provider, _provider_name = _dataset_provider("daily")
    if provider is None:
        _log_cap_unavailable("daily", "日K同步")
        return _finalize(0)
    end_time = end_date or datetime.now()
    days = count or 365
    start_time = start_date or (end_time - timedelta(days=days))
    iter_daily = getattr(provider, "iter_daily", None)
    if callable(iter_daily):

        def _tracking_chunks(chunks):
            for df in chunks:
                if not df.is_empty() and "symbol" in df.columns:
                    seen.update(df["symbol"].cast(pl.Utf8).unique().to_list())
                yield df

        return _finalize(_persist_daily_chunks(
            _tracking_chunks(iter_daily(
                symbols,
                start_time=start_time,
                end_time=end_time,
                on_chunk_done=on_chunk_done,
                **_symbol_failure_sink(provider, failed_syms),
            )),
            repo,
        ))
    df = provider.get_daily(
        symbols,
        start_time=start_time,
        end_time=end_time,
        on_chunk_done=on_chunk_done,
    )
    if df.is_empty():
        return _finalize(0)
    if "symbol" in df.columns:
        seen.update(df["symbol"].cast(pl.Utf8).unique().to_list())
    repo.append_daily(df)
    d = repo.store.data_dir.as_posix()
    repo.refresh_view("kline_daily", f"{d}/kline_daily/**/*.parquet")
    return _finalize(df.height)

# staging 缓冲的行数上限: 同一日期的 chunk 先在内存里合成一个文件再落盘。
# 内存驻留量 = 上限 + 一个 chunk, 与历史长度无关。
_STAGE_BATCH_ROWS = 200_000


def _write_daily_stage(pending: dict[str, list[pl.DataFrame]], root: Path, index: int) -> None:
    """把缓冲的各日期帧写成 staging 文件 (每个日期一个文件)。

    index 是落盘批次号: 文件名按批次递增, 合并时「后写的批次覆盖先写的」,
    与流式取数的时序一致。
    """
    for ds, frames in pending.items():
        out_dir = root / f"date={ds}"
        out_dir.mkdir(parents=True, exist_ok=True)
        df = frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal_relaxed")
        # 同一天里同一 symbol 出现多次 → 只留最后一条 (与落盘侧去重契约一致)
        df = df.unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"])
        df.write_parquet(out_dir / f"part-{index}.parquet")


def _persist_daily_chunks(chunks, repo: KlineRepository) -> int:
    """先把流式 provider 结果写入私有 staging,完整取数后再提交正式分区。"""
    staging_base = repo.store.data_dir / ".daily_sync_staging"
    _sweep_stale_daily_staging(staging_base)
    root = staging_base / uuid.uuid4().hex
    written = 0
    try:
        # chunk 逐个落文件时, 全市场一天会写出「chunk 数」个碎片 (实测 5578 个):
        # 每个小文件在本机约 20-40ms (逐文件 open/write), 一次同步光写 staging
        # 就要几分钟; 碎片数还会线性放大 DuckDB 视图注册 (逐文件读 footer +
        # union_by_name)。这里按行数上限先把多个 chunk 合成一个文件再落盘,
        # 文件数从「chunk 数 × 天数」降到「总行数 / 上限 × 天数」。
        pending: dict[str, list[pl.DataFrame]] = {}
        pending_rows = 0
        stage_index = 0
        for df in chunks:
            if df.is_empty():
                continue
            for date_df in df.partition_by("date"):
                dt = date_df["date"][0]
                ds = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
                pending.setdefault(ds, []).append(date_df)
                pending_rows += date_df.height
                written += date_df.height
            if pending_rows >= _STAGE_BATCH_ROWS:
                _write_daily_stage(pending, root, stage_index)
                stage_index += 1
                pending.clear()
                pending_rows = 0
        if pending:
            _write_daily_stage(pending, root, stage_index)

        for date_dir in sorted(root.glob("date=*")):
            files = sorted(date_dir.glob("*.parquet"))
            if not files:
                continue
            target_dir = repo.store.data_dir / "kline_daily" / date_dir.name
            target_dir.mkdir(parents=True, exist_ok=True)
            # 分区里可能同时存在历史碎片 (part-stream-*.parquet) 与正式分区
            # (part.parquet), 只读一个文件会让同一 (symbol, date) 散在多文件里重复。
            existing = sorted(target_dir.glob("*.parquet"))
            if not existing and len(files) == 1:
                # 单个 staging 文件 → 直接改名成正式分区, 不回读 staging
                # (整段历史不驻留内存, 也不产生第二个分区文件)。
                replace_with_retry(files[0], target_dir / "part.parquet")
                continue
            # 多文件或已有分区: 按日期回读合并, 内存里只驻留一天的数据。
            incoming = pl.scan_parquet(files).collect(engine="streaming")
            if existing:
                base = pl.scan_parquet(existing).collect(engine="streaming")
                incoming = pl.concat([base, incoming], how="diagonal_relaxed")
            # append_daily 内部按 (symbol, date) 去重并原子写成单个 part.parquet
            repo.append_daily(incoming)
            for part in target_dir.glob("part-stream-*.parquet"):
                part.unlink(missing_ok=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        with contextlib.suppress(OSError):
            root.parent.rmdir()

    return written


def _sweep_stale_daily_staging(staging_base, max_age_s: int = 24 * 60 * 60) -> None:
    """清理崩溃遗留的旧同步目录,不碰仍可能活跃的新目录。"""
    if not staging_base.exists():
        return
    cutoff = time.time() - max_age_s
    for run_dir in staging_base.iterdir():
        try:
            if run_dir.is_dir() and run_dir.stat().st_mtime < cutoff:
                shutil.rmtree(run_dir)
        except OSError:
            logger.warning("failed to clean stale daily staging: %s", run_dir)


def sync_daily_by_quotes(repo: KlineRepository) -> int:
    """用实时行情接口拉全市场当日数据,覆写 kline_daily 今天分区。

    一个请求覆盖 ~5500 只股票,比 batch K-line 快几个数量级。
    返回写入的行数。
    """
    provider, provider_name = _dataset_provider("realtime")
    if provider is None:
        logger.warning("实时行情源不可用, 跳过当日分区覆写")
        return 0
    try:
        resp = provider.get_realtime()
    except Exception as e:  # noqa: BLE001 — 覆写失败不阻断盘后流程
        logger.warning("实时行情快照拉取失败 (%s): %s", provider_name, e)
        return 0

    if not resp:
        logger.warning("实时行情快照为空 (%s)", provider_name)
        return 0

    records = []
    for q in resp:
        records.append({
            "symbol": q.get("symbol"),
            "open": q.get("open"),
            "high": q.get("high"),
            "low": q.get("low"),
            "close": q.get("last_price"),
            "volume": q.get("volume"),
            "amount": q.get("amount"),
            # 快照时刻标记: data_integrity 靠 quote_ts 区分盘中快照与盘后权威历史,
            # 缺失会让盘中覆写的分区在停机后被当成完整历史, 永远不进修复。
            "quote_ts": q.get("timestamp"),
        })

    df = pl.DataFrame(records)
    if df.is_empty():
        return 0
    df = df.with_columns(pl.col("quote_ts").cast(pl.Int64, strict=False))

    # 分区日期用北京交易日 (与 quote_service._build_daily 的 cn_today 一致),
    # 避免 UTC 服务器在盘中把日分区写成服务器本地日期。
    today = cn_today()
    daily_df = df.with_columns(pl.lit(today).cast(pl.Date).alias("date"))

    # 过滤停牌 (open/high 为 0; close 可能被填充为前收盘价, 不能用全零判断)
    daily_df = filter_halt_days(daily_df)

    repo.flush_live_daily(daily_df)
    logger.info("sync_daily_by_quotes: %d symbols flushed for %s", daily_df.height, today)
    return daily_df.height


# 除权事件明细列 (可选, 缺省为 null): 等差显示投影 (原始价 - Σ未来每股分红,
# 分红按其后送转比例缩放) 与官方金额全精度因子链重建使用。
# 存量 all.parquet 只有规范三列, 合并用 how="diagonal" 自动补 null;
# 读取方 (_apply_adj_factor / minute_adjust) 只按规范三列消费, 不受影响。
ADJ_DETAIL_COLS = ("dividend", "bonus", "allot", "allot_price", "prev_close")


def _normalize_adj_factor(raw) -> pl.DataFrame:
    """Normalize SDK ex_factors response to symbol/trade_date/ex_factor."""
    if raw is None or len(raw) == 0:
        return pl.DataFrame()
    if isinstance(raw, dict):
        rows: list[dict] = []
        for sym, values in raw.items():
            for item in values or []:
                row = dict(item or {})
                row.setdefault("symbol", sym)
                rows.append(row)
        df = pl.DataFrame(rows) if rows else pl.DataFrame()
    elif isinstance(raw, pl.DataFrame):
        df = raw
    else:
        df = pl.from_pandas(raw.reset_index() if hasattr(raw, "reset_index") else raw)
    if df.is_empty():
        return df
    # rename: timestamp/date → trade_date, adj_factor → ex_factor
    # 注意: 新版 SDK 可能同时返回 timestamp 和 trade_date (或 adj_factor 和 ex_factor),
    # 直接 rename 会产生重复列报错。仅当目标列不存在时才 rename。
    rename_map: dict[str, str] = {}
    for src, dst in (("timestamp", "trade_date"), ("date", "trade_date"), ("adj_factor", "ex_factor")):
        if src in df.columns and dst not in df.columns:
            rename_map[src] = dst
    df = df.rename(rename_map)
    if "trade_date" in df.columns:
        if df.schema["trade_date"] in {pl.Int64, pl.Int32, pl.UInt64, pl.UInt32, pl.Float64, pl.Float32}:
            # 毫秒时间戳 → 北京墙钟日期。不能直接 from_epoch().dt.date():
            # 那是 UTC 日期, 除权事件时间戳为北京零点 (= UTC 前一日 16:00),
            # 会整体早一天 (与 SDK True 路径 fromtimestamp(ts/1000, Asia/Shanghai) 不一致)。
            df = df.with_columns(
                _timestamp_to_beijing_datetime(pl.col("trade_date")).dt.date().alias("trade_date")
            )
        else:
            df = df.with_columns(pl.col("trade_date").cast(pl.Date, strict=False))
    if "ex_factor" in df.columns:
        df = df.with_columns(pl.col("ex_factor").cast(pl.Float64, strict=False))
    if not all(c in df.columns for c in ("symbol", "trade_date", "ex_factor")):
        return pl.DataFrame()
    # 明细列存在则透传 (缺省不影响规范三列); drop_nulls 只作用于规范三列 ——
    # 明细列合法可为 null (如纯送转事件的 dividend)。
    df = df.with_columns([
        pl.col(c).cast(pl.Float64, strict=False)
        for c in ADJ_DETAIL_COLS if c in df.columns
    ])
    cols = ["symbol", "trade_date", "ex_factor"] + [
        c for c in ADJ_DETAIL_COLS if c in df.columns
    ]
    return df.select(cols).drop_nulls(subset=["symbol", "trade_date", "ex_factor"])


def _align_adj_factor_frame(df: pl.DataFrame) -> pl.DataFrame:
    """除权因子帧对齐为统一 schema: 规范三列 + 明细列 (缺则补 null 列)。"""
    df = df.with_columns(
        pl.col("trade_date").cast(pl.Date, strict=False),
        pl.col("ex_factor").cast(pl.Float64, strict=False),
        *[pl.col(c).cast(pl.Float64, strict=False)
          for c in ADJ_DETAIL_COLS if c in df.columns],
    )
    df = df.with_columns([
        pl.lit(None, dtype=pl.Float64).alias(c)
        for c in ADJ_DETAIL_COLS if c not in df.columns
    ])
    return df.select(["symbol", "trade_date", "ex_factor", *ADJ_DETAIL_COLS])


def _recompute_adj_factors(df: pl.DataFrame) -> pl.DataFrame:
    """按明细列重算单事件因子 (公式口径修正后的存量归一化)。

    明细列 (prev_close/dividend/bonus/allot/allot_price) 是公式的唯一输入;
    存量行的 ex_factor 由旧公式算出 → 与公式漂移 (参考价差 1 分 → 因子差
    0.09% → 除权日限价差 1 分 → 收盘贴着限价时被误判成涨跌停)。
    归一化后整表同口径; 变化行会进 changed_symbols → 触发 enriched 重算。
    缺明细的行无法重算 → 保留原值。
    """
    if df.is_empty() or "prev_close" not in df.columns:
        return df

    def _recompute(row: dict) -> float | None:
        return recompute_ex_factor(
            row.get("prev_close"), row.get("dividend"), row.get("bonus"),
            row.get("allot"), row.get("allot_price"),
        )

    recomputed = (
        pl.struct(["prev_close", "dividend", "bonus", "allot", "allot_price"])
        .map_elements(_recompute, return_dtype=pl.Float64)
    )
    return df.with_columns(
        pl.when(recomputed.is_not_null())
        .then(recomputed)
        .otherwise(pl.col("ex_factor"))
        .alias("ex_factor")
    )


def recompute_adj_factor_store(out: Path, *, dry_run: bool = False) -> tuple[int, list[str]]:
    """离线归一化存量除权因子 (不联网): 按明细列重算 ex_factor。

    公式改过之后 (参考价口径从 float 换成 Decimal) 存量行的因子必须重算,
    否则整表口径不一致。除权因子同步 (任意窗口) 也会做同样的归一化,
    本函数给「不联网、只修存量」的场景用 (见 scripts/repair_adj_factor.py)。

    返回 (变化行数, 受影响标的)。幂等: 再跑一遍无变化。
    """
    if not out.exists():
        return 0, []
    existing = pl.read_parquet(out)
    merged = _recompute_adj_factors(existing)
    diffed = merged.select(["symbol", "trade_date", "ex_factor"]).join(
        existing.select(["symbol", "trade_date", "ex_factor"]),
        on=["symbol", "trade_date"], how="left", suffix="_old",
    ).filter(
        pl.col("ex_factor_old").is_null()
        | ((pl.col("ex_factor") - pl.col("ex_factor_old")).abs() > 1e-9)
    )
    changed_symbols = sorted(set(diffed["symbol"].to_list()))
    if diffed.height and not dry_run:
        _atomic_write_parquet(merged, out)
    return diffed.height, changed_symbols


def _merge_adj_factor_store(out: Path, new_data: pl.DataFrame) -> tuple[int, list[str]]:
    """合并写入 adj_factor 的 all.parquet, 返回 (新增行数, 因子发生变化的 symbol 列表)。

    how="diagonal" 兼容存量三列 schema (旧行明细自动为 null);
    unique keep="last" → 重复 (symbol, trade_date) 以本次同步为准,
    重跑一次同步即完成存量数据的明细回填。

    变化判定只看 ex_factor (新键 + 因子值差异 > 1e-9): 仅明细列回填
    (dividend/prev_close 等补 null) 不改变复权价格 → 不计入变化列表,
    enriched 局部重算因此不会被明细回填触发全市场重算。

    合并后整表按明细列归一化 (见 _recompute_adj_factors): 公式改过之后,
    存量行的因子必须重算, 否则整表口径不一致 (差异行进变化列表 → enriched 重算)。
    本次拉取的行以数据源的因子为准 (keep="last"), 归一化只作用于存量行。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    new_data = _align_adj_factor_frame(new_data)
    if not out.exists():
        _atomic_write_parquet(new_data.sort(["symbol", "trade_date"]), out)
        return new_data.height, sorted(new_data["symbol"].unique().to_list())
    existing = pl.read_parquet(out)
    before = existing.height
    merged = pl.concat([_recompute_adj_factors(existing), new_data], how="diagonal").unique(
        subset=["symbol", "trade_date"], keep="last",
    ).sort(["symbol", "trade_date"])
    # ex_factor 变化判定 (明细回填不算): 与存量按 (symbol, trade_date) 对齐,
    # 旧值缺失 (新事件) 或值差超容差即视为变化。
    diffed = merged.select(["symbol", "trade_date", "ex_factor"]).join(
        existing.select(["symbol", "trade_date", "ex_factor"]),
        on=["symbol", "trade_date"], how="left", suffix="_old",
    ).filter(
        pl.col("ex_factor_old").is_null()
        | ((pl.col("ex_factor") - pl.col("ex_factor_old")).abs() > 1e-9)
    )
    changed_symbols = sorted(set(diffed["symbol"].to_list()))
    _atomic_write_parquet(merged, out)
    return merged.height - before, changed_symbols


def sync_adj_factor(symbols: list[str], repo: KlineRepository,
                    capset: CapabilitySet,
                    start_time: datetime | None = None,
                    end_time: datetime | None = None,
                    on_chunk_done: Callable[[int, int], None] | None = None,
                    asset_type: str = "stock") -> tuple[int, list[str]]:
    """同步除权因子。走生效除权源的 provider 契约 (`get_adj_factors`)。

    支持增量: 传 start_time/end_time 只拉取该时间范围内的新除权事件。
    返回 (写入行数, 受影响的 symbol 列表) — 供 enriched 局部重算使用。
    受影响 = 因子新增或值变化 (ex_factor diff) 的标的; 仅事件明细列
    (dividend 等) 回填不改变复权价, 不计入受影响列表。
    """
    if not symbols:
        return 0, []

    chain = _provider_chain("adj_factor")
    if not chain:
        _log_cap_unavailable("adj_factor", "除权因子同步")
        return 0, []

    new_data = pl.DataFrame()
    for provider_name, provider in chain:
        new_data = provider.get_adj_factors(
            symbols,
            start_time=start_time,
            end_time=end_time,
            asset_type=asset_type,
            on_chunk_done=on_chunk_done,
        )
        if not new_data.is_empty():
            break
        # 扶摇等源对 ETF 直接空返回, 与「该 ETF 无除权」无法区分;
        # 回退链上下一源再试一次, 否则 ETF 日K永远不复权。
        if asset_type != "etf":
            return 0, []
        logger.info("除权源 %s 未返回 ETF 因子, 回退下一源", provider_name)
    if new_data.is_empty():
        return 0, []

    factor_dir = "adj_factor_etf" if asset_type == "etf" else "adj_factor"
    added, affected = _merge_adj_factor_store(
        repo.store.data_dir / factor_dir / "all.parquet", new_data
    )
    logger.info("adj_factor merged: +%d new rows, %d changed symbols, %d fetched / %d symbols",
                added, len(affected), new_data.height, len(symbols))
    return added, affected


# ===== 分钟 K 同步 =====

CANONICAL_MINUTE_COLS = [
    "symbol", "datetime", "open", "high", "low", "close", "volume", "amount",
]


# 北京墙钟特征时段(含集合竞价 09:15 与收盘 15:00): 上午 09-11, 下午 13-15
_BJ_HOURS = [9, 10, 11, 13, 14, 15]
# 上述时段 -8h 的 UTC 墙钟特征: 上午 01-03, 下午 05-07
_UTC_SHIFTED_HOURS = [1, 2, 3, 5, 6, 7]


def _enforce_minute_beijing_wallclock(df: pl.DataFrame, *, source: str) -> pl.DataFrame:
    """分钟 K datetime 时区契约守卫: 统一为北京墙钟 (naive)。

    契约 (CONTRIBUTING §3.3): kline_minute.datetime 必须是北京时间墙钟, 如 09:35:00。
    在两个源头入口强制 —— _normalize_minute 与 _try_custom_minute
    (插件/自定义源帧); 落盘 (_write_minute_partition) 与内存消费 (监控/补拉/脉冲)
    均在其下游, 这里收口即全覆盖:
    - tz-aware → 转 Asia/Shanghai 后去时区;
    - naive 且时刻落在 A 股交易时段 → 直通 (已是北京墙钟);
    - naive 且整体呈"交易时段 -8h"的 UTC 特征 → 自动 +8 纠偏并记日志;
    - 无法识别的口径 → fail-closed 抛 ValueError, 不让脏时间入库或下发。
    幂等: 纠偏后的帧再过守卫直通, 不会二次改写。
    """
    if df.is_empty() or "datetime" not in df.columns:
        return df
    dtype = df.schema["datetime"]
    if not isinstance(dtype, pl.Datetime):
        # trade_time 等字符串路径: 先解析成 Datetime (失败置 null), 再做时段分类
        if dtype == pl.Utf8:
            df = df.with_columns(pl.col("datetime").str.to_datetime(strict=False))
        else:
            df = df.with_columns(pl.col("datetime").cast(pl.Datetime("us"), strict=False))
        dtype = df.schema["datetime"]
        if not isinstance(dtype, pl.Datetime):
            return df  # 仍非 Datetime: 维持原行为交由下游处理
    if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
        df = df.with_columns(
            pl.col("datetime")
            .dt.convert_time_zone("Asia/Shanghai")
            .dt.replace_time_zone(None)
            .cast(pl.Datetime("us"))
        )
        logger.info("minute datetime tz-aware input converted to Beijing wallclock (source=%s)", source)
        return df

    hour = pl.col("datetime").dt.hour()
    beijing = int(df.select(hour.is_in(_BJ_HOURS).sum()).item() or 0)
    utc_shifted = int(df.select(hour.is_in(_UTC_SHIFTED_HOURS).sum()).item() or 0)
    if beijing == 0 and utc_shifted == 0:
        if df["datetime"].null_count() == df.height:
            return df  # 全 null: 维持原行为, 由下游落盘过滤
        raise ValueError(
            f"minute datetime 口径无法识别 (source={source}, rows={df.height}, "
            f"sample={df['datetime'].drop_nulls().head(2).to_list()}): "
            "契约要求北京墙钟 (09:30-15:00), 既非交易时段也非 UTC 平移特征"
        )
    if utc_shifted > beijing:
        if beijing:
            logger.warning(
                "minute datetime mixed convention, shifting all by +8h per UTC majority "
                "(source=%s, utc=%d, beijing=%d)", source, utc_shifted, beijing,
            )
        else:
            logger.info(
                "minute datetime UTC wallclock detected, shifted +8h to Beijing "
                "(source=%s, rows=%d)", source, utc_shifted,
            )
        return df.with_columns(pl.col("datetime") + pl.duration(hours=8))
    if utc_shifted:
        logger.warning(
            "minute datetime has %d UTC-like rows among %d Beijing rows, left as-is "
            "(source=%s)", utc_shifted, beijing, source,
        )
    return df


def _normalize_minute(df_in, default_symbol: str | None = None, *, source: str = "easytdx") -> pl.DataFrame:
    """把 SDK 返回的分钟 K 数据规范成 canonical 列 (datetime 收口为北京墙钟)。"""
    if df_in is None or len(df_in) == 0:
        return pl.DataFrame()

    if not isinstance(df_in, pl.DataFrame):
        df = pl.from_pandas(df_in.reset_index() if hasattr(df_in, "reset_index") else df_in)
    else:
        df = df_in

    rename_map = {
        "ts_code": "symbol",
        "vol": "volume",
        "amt": "amount",
    }
    df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})

    # datetime 列:优先用 timestamp(毫秒精度),其次 trade_time
    if "timestamp" in df.columns:
        # 源侧毫秒时间戳为 UTC 基准; 契约要求北京墙钟 naive
        # (统一归一口径, 见 CONTRIBUTING §3.3)
        df = df.with_columns(
            pl.from_epoch(pl.col("timestamp").cast(pl.Int64), time_unit="ms")
            .dt.replace_time_zone("UTC")
            .dt.convert_time_zone("Asia/Shanghai")
            .dt.replace_time_zone(None)
            .alias("datetime")
        ).drop("timestamp")
        for drop_col in ("trade_time", "trade_date"):
            if drop_col in df.columns:
                df = df.drop(drop_col)
    elif "trade_time" in df.columns:
        df = df.rename({"trade_time": "datetime"})
        if "trade_date" in df.columns:
            df = df.drop("trade_date")
    elif "trade_date" in df.columns:
        df = df.rename({"trade_date": "datetime"})

    if "datetime" in df.columns:
        # 时区契约守卫: 须在下方通用 us-cast 之前, 避免带时区列被静默剥成 UTC-naive
        df = _enforce_minute_beijing_wallclock(df, source=source)

    if "symbol" not in df.columns and default_symbol is not None:
        df = df.with_columns(pl.lit(default_symbol).alias("symbol"))

    # 类型规范:统一转 Datetime('us')
    if "datetime" in df.columns:
        dt_type = df.schema["datetime"]
        if not isinstance(dt_type, pl.Datetime) or dt_type.time_unit != "us":
            df = df.with_columns(pl.col("datetime").cast(pl.Datetime("us"), strict=False))

    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
    for col in ("volume", "amount"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    keep = [c for c in CANONICAL_MINUTE_COLS if c in df.columns]
    return df.select(keep)


def _compact_klines_to_df(raw, default_symbol: str | None = None) -> pl.DataFrame:
    """SDK as_dataframe=False 的 K 线原始数据 (CompactKlineData, 列式) → polars 直转。

    raw 两种形态: {symbol: Compact} (batch/universe) 或顶层 Compact (单标的)。
    列数组本身就是 polars 的原生形状 — 无 pandas 中转, 也无 SDK True 路径对
    每根 K 做的 datetime.fromtimestamp + trade_date/trade_time 字符串拼接。
    这里只搬运原始列 (timestamp 毫秒 Int64 + OHLCV), datetime/类型收口交给
    _normalize_minute / _normalize_daily — 与 True 路径同一契约。
    """
    if isinstance(raw, dict) and "timestamp" in raw:
        items: list[tuple[str | None, dict]] = [(None, raw)]
    elif isinstance(raw, dict):
        items = list(raw.items())
    else:
        return pl.DataFrame()
    frames: list[pl.DataFrame] = []
    for sym, kd in items:
        if not isinstance(kd, dict):
            continue
        ts = kd.get("timestamp") or []
        if not ts:
            continue
        key = sym if sym is not None else default_symbol
        cols: dict[str, object] = {"timestamp": ts}
        if key:
            cols["symbol"] = [key] * len(ts)
        for field in ("open", "high", "low", "close", "volume", "amount"):
            values = kd.get(field)
            if values is not None:
                cols[field] = values
        frames.append(pl.DataFrame(cols))
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def _timestamp_to_beijing_datetime(col: pl.Expr) -> pl.Expr:
    """timestamp (UTC 毫秒) → 北京墙钟 naive datetime 表达式。

    与 SDK True 路径 trade_date 列同口径: datetime.fromtimestamp(ts/1000, Asia/Shanghai)。
    """
    return (
        pl.from_epoch(col.cast(pl.Int64), time_unit="ms")
        .dt.replace_time_zone("UTC")
        .dt.convert_time_zone("Asia/Shanghai")
        .dt.replace_time_zone(None)
    )


def _datetime_to_ms(dt: datetime) -> int:
    """datetime → 毫秒时间戳 (供 SDK start_time / end_time 使用)。"""
    return int(dt.timestamp() * 1000)


def _write_minute_partition(df: pl.DataFrame, minute_dir) -> int:
    """按 _trade_date 分区落盘分钟 K (读旧→concat→unique→原子写)。返回写入行数。

    抽自原 sync_and_persist_minute 末尾的循环, 供流式落盘 (每段一次) 与一次性迁移共用。
    """
    if df.is_empty():
        return 0
    df = df.with_columns(pl.col("datetime").dt.date().alias("_trade_date"))
    written = 0
    for day_df in df.partition_by("_trade_date"):
        trade_date = day_df["_trade_date"][0]
        out = minute_dir / f"date={trade_date}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            existing = pl.read_parquet(out)
            if "datetime" in existing.columns:
                existing = existing.filter(pl.col("datetime").is_not_null())
            new_rows = day_df.drop("_trade_date")
            # 只对本次触及的 symbol 合并去重, 未触及行原样保留: 单股补齐时避免
            # 为合并几行数据把全市场分区整体 unique 的写放大 (issue #305)。
            touched = new_rows["symbol"].unique().to_list()
            same = existing.filter(pl.col("symbol").is_in(touched))
            other = existing.filter(~pl.col("symbol").is_in(touched))
            merged = pl.concat([same, new_rows], how="diagonal_relaxed").unique(
                subset=["symbol", "datetime"], keep="last",
            )
            day_df = pl.concat([other, merged], how="diagonal_relaxed")
        else:
            day_df = day_df.drop("_trade_date")
        day_df = day_df.sort("symbol", "datetime")
        _atomic_write_parquet(day_df, out)
        written += day_df.height
    return written


def _selected_minute_provider_name() -> str:
    """识别偏好 getter 静默回退的失效分钟源。

    getter 在存的源不可用时静默回退默认源 —— 用户看到的还是「生效」, 但数据
    实际来自别的源。这里把这种情况显式标出来, 供上层 fail-closed。
    """
    saved = str(preferences.load().get("minute_data_provider") or "").lower()
    selected = preferences.get_minute_data_provider()
    if saved and saved != selected:
        return f"unavailable:{saved}"
    return selected


def _resolve_minute_provider(
    provider_name: str,
) -> tuple[object | None, str | None]:
    """统一解析分钟源, 把所有 resolver 调用纳入同一异常边界。

    供分钟拉取和落盘共用，解析失败统一返回错误，不跨源补数据。

    返回 (provider, error_msg):
      - 源可用 → (provider, None)
      - 源未配置 minute dataset 或解析失败 → (None, error_msg)

    用户明确选择的源不提供分钟数据时 fail-closed —— 不静默换源取数,
    避免同一批分钟K混进两个源的数据。
    注意: provider.get_minute() 仍由调用方在自身 try 块内调用 (业务异常, 非解析异常)。
    """
    from app.data_providers import custom as custom_sources
    try:
        provider = custom_sources.get_provider(provider_name)
        datasets = provider.config.datasets
    except Exception as e:  # noqa: BLE001
        return (None, str(e))
    if "minute" not in datasets:
        return (None, f"provider {provider_name} does not configure minute data")
    return (provider, None)


def _try_custom_minute(
    symbols: list[str],
    start_time: datetime | None,
    end_time: datetime | None,
    asset_type: AssetType,
    freq: str = "1m",
    on_chunk_done: Callable[[int, int, str], None] | None = None,
) -> tuple[pl.DataFrame | None, bool]:
    """从生效分钟源拉取。返回 (df, resolved)。

    返回契约:
      (None, False) → 没有可用分钟源 (解析失败) → 调用方按「能力不可用」处理
      (df, True)    → 源已解析并调用过 (含空 df) → 直接使用结果, 不跨源

    用户明确选择了某个分钟源后,其请求失败或解析异常均 fail-closed;
    避免静默跨源请求把两个源的数据混进同一批分钟K。

    resolver 异常边界由 _resolve_minute_provider 统一兜底; 业务调用
    (provider.get_minute) 仍在本函数 try 块内, 与 resolver 异常分离
    便于日志区分 ("resolution failed" vs "call failed")。

    on_chunk_done 适配: 上层回调是 3 参 (cur, total, seg_label), provider
    实现内部以 2 参 (cur, total) 调用。这里包装一层, provider 调 2 参时补
    默认 seg_label="custom" 转发给上层, 保证进度展示不降级。
    """
    provider_name = _selected_minute_provider_name()
    if provider_name.startswith("unavailable:"):
        logger.warning("分钟源 %s 不可用, 已回退默认源; 本轮不取数",
                       provider_name.removeprefix("unavailable:"))
        return (None, False)
    provider, err = _resolve_minute_provider(provider_name)
    if err is not None or provider is None:
        logger.warning("分钟源 %s 没有可用的 minute 数据集: %s", provider_name, err)
        return (None, False)

    # 包装 on_chunk_done: provider 调 2 参 → 补 seg_label="custom" → 转发上层 3 参
    wrapped_cb: Callable[[int, int], None] | None = None
    if on_chunk_done is not None:
        def _wrapped_cb(cur: int, total: int) -> None:
            on_chunk_done(cur, total, "custom")
        wrapped_cb = _wrapped_cb

    try:
        df = provider.get_minute(
            symbols, start_time=start_time, end_time=end_time,
            asset_type=asset_type, freq=freq, on_chunk_done=wrapped_cb,
        )
    except ProviderRequestError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("分钟源 %s 调用失败: %s", provider_name, e)
        return (pl.DataFrame(), True)
    try:
        # 时区契约守卫: 插件/自定义源帧同样收口为北京墙钟 (CONTRIBUTING §3.3)
        df = _normalize_minute(df, source=provider_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("分钟源 %s datetime 契约校验失败: %s", provider_name, e)
        return (pl.DataFrame(), True)
    return (df, True)


def sync_minute_batch(
    symbols: list[str],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    count: int | None = None,
    batch_size: int | None = None,
    rpm: int | None = None,
    on_chunk_done: Callable[[int, int, str] | None] | None = None,
    segment_trading_days: int = 20,
    on_segment: Callable[[pl.DataFrame], None] | None = None,
    asset_type: AssetType = "stock",
    raw_basis: bool = False,
) -> pl.DataFrame:
    """批量拉取多股分钟 K (走生效分钟源的 provider 契约)。

    优先使用 start_time / end_time 区间, 确保所有标的覆盖同一时间段。
    on_chunk_done(current, total) 每个 chunk 完成后回调。

    segment_trading_days / batch_size / rpm / count: 源侧自带节流与分段,
    这几个参数保留只为兼容调用方签名。
    on_segment: 每个分段拉完后回调 (传入该段拼接后的 DataFrame)。
        传入时走「流式落盘」: 段内结果累积到 seg_out, 段末 concat 后回调并清空,
        不进入全局 out → 内存峰值从「全量」降到「单段」。适用于 sync_and_persist_minute。
        不传时 (如 get_minute_batch 的实时补拉) 保持原契约: 累积进 out 末尾一次性返回。
    """
    # 窗口两端统一为北京时区: naive 值按北京墙钟解释 (分钟 K 落盘口径),
    # 与服务器本地时区无关; 源侧拿到的始终是带北京时区的窗口。
    start_time = _as_beijing(start_time) if start_time is not None else None
    end_time = _as_beijing(end_time) if end_time is not None else None
    if on_segment is not None:
        # 流式落盘(持久化路径, 如 sync_and_persist_minute): 按块拉取,
        # 每块立即 on_segment 落盘。原实现整表攒齐后一次落盘, 全市场同步在
        # 中途重启/异常时全量丢弃(实测 2026-09-30: 4h 进度归零)。
        # 源选中后的失败 fail-closed 返回空 df, 不跨源回退。
        # 已落盘块由分区 merge-upsert 幂等去重。
        chunk_list = list(chunked(symbols, _CUSTOM_MINUTE_CHUNK)) or []
        for idx, chunk_syms in enumerate(chunk_list, start=1):
            sub_df, resolved = _try_custom_minute(
                chunk_syms, start_time=start_time, end_time=end_time,
                asset_type=asset_type, freq="1m", on_chunk_done=None,
            )
            if not resolved:
                return pl.DataFrame()
            if sub_df is not None and not sub_df.is_empty():
                on_segment(sub_df)
            if on_chunk_done is not None:
                on_chunk_done(idx, len(chunk_list), "custom")
        return pl.DataFrame()

    df, resolved = _try_custom_minute(
        symbols, start_time=start_time, end_time=end_time,
        asset_type=asset_type, freq="1m", on_chunk_done=on_chunk_done,
    )
    if not resolved:
        return pl.DataFrame()
    return df if df is not None else pl.DataFrame()


def intraday_monitor_support(capset: CapabilitySet | None) -> dict[str, object]:
    """返回分时信号监控可用的数据能力和单轮标的上限。"""
    provider_name = _selected_minute_provider_name()
    _provider, error = _resolve_minute_provider(provider_name)
    if error is not None:
        logger.warning("minute provider resolution failed while checking monitor support: %s", error)
        return {
            "available": False, "source": None, "max_symbols": 0,
            "reason": "当前分钟数据源不可用，请检查数据源配置",
        }
    limits = capset.limits(Cap.KLINE_MINUTE_BATCH) if capset and capset.has(Cap.KLINE_MINUTE_BATCH) else None
    return {
        "available": True, "source": "minute",
        "max_symbols": max(1, int(limits.batch or 100)) if limits else 100,
        "reason": "使用已配置的分钟数据源",
    }


def fetch_intraday_monitor_batch(
    symbols: list[str], capset: CapabilitySet | None, *, now: datetime | None = None,
) -> pl.DataFrame:
    """按当前能力获取分时信号所需的当日分钟数据，不落盘。"""
    if not symbols:
        return pl.DataFrame()
    support = intraday_monitor_support(capset)
    if not support["available"] or len(symbols) > int(support["max_symbols"]):
        return pl.DataFrame()

    now = now or cn_now()
    start_time = now.replace(hour=9, minute=25, second=0, microsecond=0)
    limits = capset.limits(Cap.KLINE_MINUTE_BATCH) if capset and capset.has(Cap.KLINE_MINUTE_BATCH) else None
    return sync_minute_batch(
        symbols, start_time=start_time, end_time=now,
        batch_size=limits.batch if limits else None,
        rpm=limits.rpm if limits else None,
    )


def _resolve_full_minute_provider(
    provider_name: str,
) -> tuple[object | None, str | None]:
    """解析全量分钟生效源。返回 (provider, error_msg):

    - 源缺少 full_minute 或解析失败 → (None, error_msg)
    - 成功 → (provider, None)
    """
    from app.data_providers import custom as custom_sources
    try:
        provider = custom_sources.get_provider(provider_name)
        datasets = provider.config.datasets
    except Exception as e:  # noqa: BLE001
        return (None, str(e))
    if "full_minute" not in datasets:
        return (None, f"provider {provider_name} does not configure full_minute data")
    return (provider, None)


def fetch_intraday_custom_batch(
    provider: object,
    provider_name: str,
    symbols: list[str],
) -> tuple[pl.DataFrame, int]:
    """自定义源全量分钟修复轮: 当日窗口全市场批量拉取 (不落盘)。

    provider 契约 (见 docs/plugin-development.md):
    - get_intraday_batch(symbols, count, asset_type) 优先 — 源自管批量端点;
    - 未实现则回退 get_minute(symbols, 当日窗口) — 复用逐标的分钟K机制,
      请求数按 chunk 回调统计。
    帧统一过北京墙钟守卫 (与 _try_custom_minute 同纪律)。
    返回 (当日分钟K, 请求数); 失败返回空 df 由调用方按空轮处理。
    """
    try:
        method = getattr(provider, "get_intraday_batch", None)
        if callable(method):
            df = method(symbols)
            requests = 1
        else:
            counted = {"requests": 0}

            def _count_requests(cur: int, total: int) -> None:
                counted["requests"] = max(counted["requests"], int(total))

            end = cn_now()
            start = end.replace(hour=0, minute=0, second=0, microsecond=0)
            df = provider.get_minute(
                symbols, start_time=start, end_time=end,
                asset_type="stock", freq="1m", on_chunk_done=_count_requests,
            )
            requests = counted["requests"]
    except Exception as e:  # noqa: BLE001
        logger.warning("custom full_minute batch via %s failed: %s", provider_name, e)
        return (pl.DataFrame(), 0)
    try:
        df = _enforce_minute_beijing_wallclock(df, source=provider_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("custom full_minute datetime 契约校验失败 (%s): %s", provider_name, e)
        return (pl.DataFrame(), 0)
    return (df, max(requests, 1))


def fetch_intraday_custom_latest(
    provider: object,
    provider_name: str,
    *,
    count: int = 3,
) -> tuple[pl.DataFrame, int] | None:
    """自定义源全量分钟稳态增量轮 (不落盘)。

    provider 可选实现 get_intraday_latest(symbols=None, count) → 每只标的最新
    count 根分钟K, 尽量单请求/低请求量 (全市场分时快照同义)。
    未实现返回 None — 调用方降级为仅修复轮模式。失败返回 (空 df, 0) 按空轮处理。
    """
    method = getattr(provider, "get_intraday_latest", None)
    if not callable(method):
        return None
    try:
        df = method(count=count)
    except Exception as e:  # noqa: BLE001
        logger.warning("custom full_minute latest via %s failed: %s", provider_name, e)
        return (pl.DataFrame(), 0)
    try:
        df = _enforce_minute_beijing_wallclock(df, source=provider_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("custom full_minute datetime 契约校验失败 (%s): %s", provider_name, e)
        return (pl.DataFrame(), 0)
    return (df, 1)


def fetch_minute_single(
    symbol: str,
    trade_date: date,
    asset_type: AssetType = "stock",
    *,
    capset: CapabilitySet,
    raw_basis: bool = False,
) -> pl.DataFrame:
    source = preferences.data_source_stamp()
    key = (source, capset, _selected_minute_provider_name(), symbol, trade_date, asset_type, raw_basis)

    def fetch():
        frame = _fetch_minute_single(symbol, trade_date, asset_type)
        if source != preferences.data_source_stamp():
            raise WorkBusyError("分钟数据源配置已变化，请重新加载")
        return frame

    return _single_minute_work.run(key, fetch, ttl_s=2)

def _fetch_minute_single(
    symbol: str, trade_date: date, asset_type: AssetType,
) -> pl.DataFrame:
    """实时拉取单股单日分钟 K(不写入本地)。

    只走生效分钟源的 provider 契约; 源不可用或请求失败时 fail-closed,
    不跨源补数据, 避免同一批分钟K混进两个源。
    """
    from datetime import datetime
    # 北京时间窗口必须带时区: naive datetime 会被 .timestamp() 按服务器本地时区解释,
    # UTC 容器上窗口整体偏移 8 小时, 分时补拉必然为空。
    start_time = datetime(trade_date.year, trade_date.month, trade_date.day, 9, 25, 0, tzinfo=CN_TZ)
    end_time = datetime(trade_date.year, trade_date.month, trade_date.day, 15, 5, 0, tzinfo=CN_TZ)

    df, resolved = _try_custom_minute(
        [symbol], start_time=start_time, end_time=end_time,
        asset_type=asset_type, freq="1m",
    )
    if not resolved or df is None:
        return pl.DataFrame()
    return df


def fetch_adj_factor_single(
    symbol: str, *, asset_type: AssetType = "stock", strict: bool = False,
) -> pl.DataFrame:
    """按已配置的数据源获取单标的除权因子，不写入本地。

    返回结构: symbol, trade_date, ex_factor (空 DataFrame 表示无除权事件或拉取失败)。
    与 _apply_adj_factor / compute_enriched 的 factors 参数格式一致。
    """
    chain = _provider_chain("adj_factor")
    if not chain:
        return pl.DataFrame()
    for provider_name, provider in chain:
        try:
            frame = provider.get_adj_factors(
                [symbol], start_time=None, end_time=None, asset_type=asset_type,
            )
        except Exception as e:
            logger.warning("fetch_adj_factor_single(%s, %s) failed: %s", symbol, provider_name, e)
            if strict:
                raise ProviderRequestError("复权因子获取失败") from e
            continue
        if not frame.is_empty() or asset_type != "etf":
            return frame
    return pl.DataFrame()


def _as_beijing(d: datetime) -> datetime:
    """落盘的分钟 datetime 是北京墙钟 naive, 带上北京时区再交给取数窗口。

    naive 值经 _datetime_to_ms 会被 .timestamp() 按服务器本地时区解释, 与同
    窗口另一端的服务器本地时间混用后整体错位 (UTC 容器上错 8 小时)。
    """
    return d if d.tzinfo is not None else d.replace(tzinfo=CN_TZ)


def _latest_minute_datetime(repo: KlineRepository, asset_type: AssetType = "stock") -> datetime | None:
    """本地分钟 K 数据的最新时间 (北京时区)。"""
    try:
        table = "kline_etf_minute" if asset_type == "etf" else "kline_minute"
        res = repo.execute_one(f"SELECT max(datetime) FROM {table}")
        if res and res[0]:
            d = res[0]
            if isinstance(d, datetime):
                return _as_beijing(d)
            return _as_beijing(datetime.fromisoformat(str(d)))
    except Exception:  # noqa: BLE001
        pass
    return None


def _earliest_minute_datetime(repo: KlineRepository, asset_type: AssetType = "stock") -> datetime | None:
    """本地分钟 K 数据的最早时间 (北京时区, 用于向前扩展的起点)。"""
    try:
        table = "kline_etf_minute" if asset_type == "etf" else "kline_minute"
        res = repo.execute_one(f"SELECT min(datetime) FROM {table}")
        if res and res[0]:
            d = res[0]
            if isinstance(d, datetime):
                return _as_beijing(d)
            return _as_beijing(datetime.fromisoformat(str(d)))
    except Exception:  # noqa: BLE001
        pass
    return None


def _cleanup_null_datetime_minute(repo: KlineRepository) -> None:
    """检测并清除 datetime 全为 null 的旧版分钟 K 数据(迁移用)。"""
    minute_dir = repo.store.data_dir / "kline_minute"
    if not minute_dir.exists():
        return
    try:
        row = repo.execute_one(
            "SELECT count(*) AS total, count(datetime) AS non_null FROM kline_minute"
        )
        if row and row[0] > 0 and (row[1] is None or row[1] == 0):
            # 全部 datetime 为 null — 清除所有分钟 K parquet
            n = 0
            for f in minute_dir.rglob("*.parquet"):
                f.unlink()
                n += 1
            logger.info("cleaned %d corrupted minute-K parquet files (null datetime)", n)
    except Exception as e:  # noqa: BLE001
        logger.debug("minute cleanup check failed: %s", e)


def _migrate_symbol_to_date_partition(repo: KlineRepository) -> None:
    """将旧版 symbol= 分区迁移为 date= 分区。迁移完成后删除旧目录。"""
    minute_dir = repo.store.data_dir / "kline_minute"
    if not minute_dir.exists():
        return

    old_dirs = [d for d in minute_dir.iterdir() if d.is_dir() and d.name.startswith("symbol=")]
    if not old_dirs:
        return

    logger.info("migrating %d symbol-partitioned minute-K dirs to date partition…", len(old_dirs))

    all_frames: list[pl.DataFrame] = []
    for sym_dir in old_dirs:
        for pq in sym_dir.glob("*.parquet"):
            try:
                df = pl.read_parquet(pq)
                if "datetime" in df.columns:
                    df = df.filter(pl.col("datetime").is_not_null())
                if not df.is_empty():
                    all_frames.append(df)
            except Exception:  # noqa: BLE001
                pass

    if not all_frames:
        # 数据全部不可用，直接删旧目录
        for d in old_dirs:
            d.mkdir(parents=True, exist_ok=True)
            for f in d.rglob("*"):
                if f.is_file():
                    f.unlink()
            d.rmdir()
        return

    combined = pl.concat(all_frames, how="diagonal_relaxed")
    combined = combined.unique(subset=["symbol", "datetime"], keep="last")

    # 按日期写新分区
    combined = combined.with_columns(pl.col("datetime").dt.date().alias("_trade_date"))
    for day_df in combined.partition_by("_trade_date"):
        trade_date = day_df["_trade_date"][0]
        out = minute_dir / f"date={trade_date}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        day_df = day_df.drop("_trade_date").sort("symbol", "datetime")
        _atomic_write_parquet(day_df, out)

    # 删旧目录
    for d in old_dirs:
        for f in d.rglob("*"):
            if f.is_file():
                f.unlink()
        # 移除空目录
        try:
            d.rmdir()
        except OSError:
            pass

    logger.info("minute-K migration done: %d rows migrated", combined.height)


def sync_and_persist_minute(
    symbols: list[str],
    repo: KlineRepository,
    capset: CapabilitySet,
    days: int = 5,
    on_chunk_done: Callable[[int, int, str], None] | None = None,
    extend_backward: bool = False,
    force_full_days: bool = False,
    *,
    asset_type: AssetType | None = None,
) -> int:
    """同步分钟 K 并存到 Parquet。返回写入行数。

    存储口径由基准标记决定 (services/minute_adjust): 存量未迁移 → SDK adjust=qfq
    前复权 (旧行为); 已迁移 → adjust='none' 原始价落盘, 复权读取时投影。
    使用 start_time / end_time 区间拉取, 确保所有标的覆盖同一时间段。
    on_chunk_done(current, total) 每个 chunk 完成后回调。
    force_full_days=True 时强制回溯 days 自然日 (不增量补, 用于个股补齐历史)。
    未指定资产类型时按维表拆分股票/ETF，分别取数、落盘与刷新视图。
    """
    if not symbols:
        return 0
    if asset_type is None:
        etf_set = repo.get_etf_symbol_set()
        etf_symbols = [symbol for symbol in symbols if symbol in etf_set]
        if etf_symbols:
            stock_symbols = [symbol for symbol in symbols if symbol not in etf_set]
            written = 0
            for asset, group in (("stock", stock_symbols), ("etf", etf_symbols)):
                if group:
                    written += sync_and_persist_minute(
                        group, repo, capset, days=days, on_chunk_done=on_chunk_done,
                        extend_backward=extend_backward, force_full_days=force_full_days,
                        asset_type=asset,
                    )
            return written
        asset_type = "stock"
    if asset_type not in {"stock", "etf"}:
        raise ValueError("分钟落库同步仅支持股票和 ETF")

    minute_table = "kline_etf_minute" if asset_type == "etf" else "kline_minute"
    source_stamp = preferences.data_source_stamp()
    minute_provider = _selected_minute_provider_name()
    # 自定义源的能力和请求失败时都 fail-closed；共享能力集可能包含第三方源增补。
    _provider, resolve_err = _resolve_minute_provider(minute_provider)
    if resolve_err is not None:
        logger.warning(
            "custom minute provider %s resolution failed at sync_and_persist_minute: %s",
            minute_provider, resolve_err,
        )
        return 0

    # 迁移:旧版 _normalize_minute 未转换 timestamp→datetime,导致全部 datetime 为 null
    # 检测到后直接清除(这些数据无法使用)
    if asset_type == "stock":
        _cleanup_null_datetime_minute(repo)
        # 迁移:旧版按 symbol= 分区转为 date= 分区
        _migrate_symbol_to_date_partition(repo)

    # 窗口两端统一为北京时区: 起止点会与本地分钟 K 的北京墙钟混用, 用服务器
    # 本地时间会让窗口整体错位 (UTC 容器上起点晚于终点, 增量补拉一个请求都发不出)。
    now = cn_now()

    if extend_backward:
        # 向前扩展模式: 从本地最早数据往前补, 叠加已有数据避免缺口。
        earliest_dt = _earliest_minute_datetime(repo, asset_type)
        # 按交易日换算自然日 (7/5 系数)。>41 交易日时 +10 天余量覆盖节假日。
        # (分段由 sync_minute_batch 的 segment_trading_days 控制, 与此处的区间天数独立。)
        calendar_days = int(days * 7 / 5) + (10 if days > 41 else 0)
        if earliest_dt:
            end_time = earliest_dt
            start_time = end_time - timedelta(days=calendar_days)
        else:
            # 本地无数据 → 从今天往前拉
            start_time = now - timedelta(days=calendar_days)
            end_time = now
    else:
        # 默认增量模式: 首次拉取回溯 N 天, 已有数据则从最新时间增量补到今天
        # force_full_days=True: 强制回溯 days 自然日 (个股补齐历史, 不增量)
        last_dt = _latest_minute_datetime(repo, asset_type)
        if force_full_days:
            # 按交易日换算自然日 (7/5 系数), 确保覆盖足够交易日
            calendar_days = int(days * 7 / 5) + 5
            start_time = now - timedelta(days=calendar_days)
        elif last_dt:
            start_time = last_dt
        else:
            start_time = now - timedelta(days=days)
        end_time = now

    limit = resolve_limit(
        capset,
        Cap.KLINE_MINUTE_BATCH,
        default_batch=100,
        default_rpm=30,
        default_rpm_when_unset=False,
    )

    # 流式落盘: 每段拉完立即写盘, 内存峰值 = 单段 (而非全量)。
    # 全量攒内存曾导致 1 年全市场分钟 K OOM 卡死 (3 亿行 / 数十 GB)。
    minute_dir = repo.store.data_dir / minute_table
    written_box = [0]  # list 闭包, 绕过 Python 闭包外层赋值

    def _persist(seg_df: pl.DataFrame) -> None:
        if source_stamp != preferences.data_source_stamp():
            raise WorkBusyError("分钟数据源配置已变化，请重新补齐")
        # 单股自动补齐可能与另一个补齐请求同时写同一日期分区。Windows 不允许
        # 替换仍被另一写入占用的临时文件,因此读-改-写必须复用仓库写锁。
        with repo._write_lock:
            written_box[0] += _write_minute_partition(seg_df, minute_dir)

    segment_days = preferences.get_minute_sync_segment_days()
    try:
        sync_minute_batch(
            symbols, start_time=start_time, end_time=end_time,
            batch_size=limit.batch, rpm=limit.rpm,
            on_chunk_done=on_chunk_done,
            segment_trading_days=segment_days,
            on_segment=_persist,
            asset_type=asset_type,
            raw_basis=minute_adjust.minute_basis_is_raw(repo.store.data_dir),
        )
    finally:
        # 后续批次失败时，先前已落盘的股票/ETF 分区仍需可查询。
        if written_box[0]:
            d = repo.store.data_dir.as_posix()
            repo.refresh_view(minute_table, f"{d}/{minute_table}/**/*.parquet")

    if written_box[0] == 0:
        return 0
    written = written_box[0]

    logger.info("minute K synced: %d rows (%d symbols)", written, len(symbols))
    return written
