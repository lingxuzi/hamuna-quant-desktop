"""标的维表同步服务。

维表跟随日K数据源: 生效源实现 get_instruments 时直接用它拉全量标的元数据,
flatten ext 字段写入 instruments.parquet; 生效源不提供维表时回退默认源
(内置 easytdx) —— 二者天然耦合, 没有独立偏好项。
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date
from pathlib import Path

import polars as pl

from app.market_time import cn_today
from app.services.fs_utils import atomic_write_parquet

logger = logging.getLogger(__name__)

# 注册制新股「上市前 N 个交易日无涨跌幅限制」规则的起点 (沪深主板)。
# 早于该日上市的老股不适用窗口, 见 app.price_limits。
_REGISTRATION_DATE = date(2023, 2, 17)
# 上市日回填的「拥挤日」阈值: 同一个首日落在这么多标的身上, 说明这个日期是
# 同步窗口批量落库的特征 (历史起点) 而不是真实上市日, 一律不回填。
_LISTING_BACKFILL_CROWDED_MIN = 15


def _flatten_instruments(items: list[dict], *, current_limits: bool = True) -> list[dict]:
    """把 SDK 返回的 Instrument 列表 flatten 成扁平行。"""
    rows = []
    for item in items:
        row = {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "code": item.get("code"),
            "exchange": item.get("exchange"),
            "region": item.get("region"),
            "type": item.get("type"),
        }
        ext = item.get("ext") or {}
        row["listing_date"] = ext.get("listing_date")
        row["total_shares"] = ext.get("total_shares")
        row["float_shares"] = ext.get("float_shares")
        row["tick_size"] = ext.get("tick_size")
        row["limit_up"] = ext.get("limit_up") if current_limits else None
        row["limit_down"] = ext.get("limit_down") if current_limits else None
        rows.append(row)
    return rows


def fetch_instruments_via_provider(asset_type: str = "stock") -> list[dict] | None:
    """用生效日K源 (或其默认源) 拉标的维表。

    标的维表跟随日K数据源(二者天然耦合, 无独立偏好项)。生效源不实现
    get_instruments 时 (如 fuyao) 回退默认源 (内置 easytdx)。

    返回 flatten 行列表; 所有候选源都拿不到维表时返回 None。
    asset_type: stock=A股维表, index/etf=指数/ETF 维表 (index_sync 复用)。
    """
    from app.data_providers import custom as custom_sources
    from app.data_providers.capabilities import default_provider_for
    from app.services import preferences

    effective = preferences.get_daily_data_provider()
    for provider_name in (effective, default_provider_for("daily")):
        if not provider_name or not custom_sources.is_custom_provider(provider_name):
            continue
        provider = custom_sources.get_provider(provider_name)
        if not hasattr(provider, "get_instruments"):
            continue
        try:
            items = provider.get_instruments(asset_type) or []
        except Exception as e:  # noqa: BLE001
            logger.warning("provider %s get_instruments(%s) 失败: %s", provider_name, asset_type, e)
            continue
        rows = _flatten_instruments(items)
        logger.info("instruments via %s: %d %s", provider_name, len(rows), asset_type)
        return rows
    logger.warning("维表源不可用 (%s/%s), 本轮跳过标的维表同步", effective, asset_type)
    return None


def sync_instruments(data_dir: Path) -> int:
    """全量同步标的维表 → data/instruments/instruments.parquet。

    返回写入的行数。
    """
    all_rows = fetch_instruments_via_provider("stock") or []

    if not all_rows:
        return 0

    df = pl.DataFrame(all_rows)
    df = _preserve_share_fields(df, data_dir)
    df = _derive_float_shares(df, data_dir)
    df = _backfill_listing_dates(df, data_dir)
    today = cn_today()
    df = df.with_columns(pl.lit(today).alias("as_of"))

    out = data_dir / "instruments" / "instruments.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(df, out)

    try:
        changed = _update_name_history(df, data_dir, today)
        if changed:
            logger.info("instruments 名称变更 %d 条 → name_history", changed)
    except Exception as e:  # noqa: BLE001
        logger.warning("名称变更历史写入失败 (不影响维表同步): %s", e)

    logger.info("instruments synced: %d rows → %s", df.height, out)
    return df.height


# 股本与静态交易规则字段: 部分行情源的标的维表只有代码/名称/市场/类型。
# 这些字段缺失会让 enriched 的 turnover_rate / 市值为空, 进而使
# 依赖换手率与流通市值的策略筛不出任何标的 —— 因此绝不能用 null 覆盖已有值。
# 涨跌停价随交易日变化，不从历史维表继承。
_SHARE_FIELDS = (
    "listing_date", "total_shares", "float_shares", "tick_size",
)


def _preserve_share_fields(df: pl.DataFrame, data_dir: Path) -> pl.DataFrame:
    """用现有维表补全 provider 未提供的股本/交易规则字段 (按 symbol 左连接)。

    新增标的仍保留 null (没有历史值可用, 不伪造); 只在同一 symbol 上把
    provider 的空值替换为既有值, 并在日志里给出补全规模。
    """
    available = [c for c in _SHARE_FIELDS if c in df.columns]
    if not available:
        return df
    if all(df[c].null_count() == 0 for c in available):
        return df  # provider 已提供全部字段, 无需补全

    path = data_dir / "instruments" / "instruments.parquet"
    if not path.exists():
        logger.warning(
            "instruments provider 未提供股本字段且无历史维表可补全: "
            "enriched 换手率/市值将为 null, 依赖它们的策略会筛不出标的"
        )
        return df
    try:
        existing = pl.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("instruments 历史维表读取失败, 无法补全股本字段: %s", e)
        return df
    if "symbol" not in existing.columns:
        return df

    keep = ["symbol", *[c for c in available if c in existing.columns]]
    if len(keep) <= 1:
        return df
    merged = df.join(existing.select(keep), on="symbol", how="left", suffix="_prev")
    filled: list[str] = []
    for col in keep[1:]:
        prev = f"{col}_prev"
        if prev not in merged.columns:
            continue
        # 逐行 coalesce: provider 有值用 provider, 为空才回落历史值。这样
        # 「部分标的缺字段」也能补齐, 而不只处理整列为空的源。
        if merged[col].null_count() and merged[prev].is_not_null().any():
            merged = merged.with_columns(
                pl.coalesce(pl.col(col), pl.col(prev)).alias(col)
            )
            filled.append(col)
    merged = merged.drop([f"{c}_prev" for c in keep[1:] if f"{c}_prev" in merged.columns])
    if filled:
        restored = int(merged.select(
            [pl.col(c).is_not_null().sum() for c in filled]
        ).row(0)[0])
        logger.info(
            "instruments provider 缺 %s, 已逐行从历史维表补全 (首列非空 %d/%d)",
            "/".join(filled), restored, merged.height,
        )
    return merged


def _derive_float_shares(df: pl.DataFrame, data_dir: Path) -> pl.DataFrame:
    """用当日 enriched 的 turnover_rate 反推流通股本, 补 provider 拿不到的标的。

    没有任何数据源提供股本 (easytdx 维表不含, fuyao 无 shares 接口), 换手率只能
    由实时快照的 turnover 字段直接写下。反过来, 有了 volume 与 turnover_rate 就能
    还原 float_shares = volume(手) * 10000 / turnover_rate(%) —— 用最新一个有
    换手率的 enriched 分区做, 只补仍然为空的标的。

    反推值同时填入 total_shares: 策略的市值条件用 close * total_shares, 只补
    float_shares 照样筛不出标的。总股本通常大于等于流通股本, 用流通股本做下限
    只会低估市值, 比留空 (市值条件直接判 false) 更接近真实。
    """
    if "float_shares" not in df.columns or "symbol" not in df.columns:
        return df
    missing = df.filter(
        pl.col("float_shares").is_null()
        | ("total_shares" in df.columns and pl.col("total_shares").is_null())
    )
    if missing.is_empty():
        return df

    enriched_dir = data_dir / "kline_daily_enriched"
    if not enriched_dir.exists():
        return df
    snapshots = sorted(enriched_dir.glob("date=*/part.parquet"), reverse=True)[:5]
    derived = pl.DataFrame()
    for path in snapshots:
        try:
            frame = pl.read_parquet(path, columns=["symbol", "volume", "turnover_rate"])
        except Exception:  # noqa: BLE001
            continue
        valid = frame.filter(
            (pl.col("turnover_rate") > 0) & (pl.col("volume") > 0)
        )
        if valid.is_empty():
            continue
        derived = valid.with_columns(
            (pl.col("volume") * 10000.0 / pl.col("turnover_rate")).alias("float_shares")
        ).select("symbol", "float_shares")
        break
    if derived.is_empty():
        logger.warning(
            "维表缺股本且无带换手率的 enriched 分区可反推: "
            "市值/换手率条件将筛不出标的 (先跑一轮实时行情)"
        )
        return df

    merged = df.join(
        derived.unique(subset=["symbol"]), on="symbol", how="left", suffix="_derived",
    )
    filled = merged.with_columns(
        pl.coalesce(pl.col("float_shares"), pl.col("float_shares_derived")).alias("float_shares")
    )
    if "total_shares" in filled.columns:
        filled = filled.with_columns(
            pl.coalesce(pl.col("total_shares"), pl.col("float_shares")).alias("total_shares")
        )
    filled = filled.drop("float_shares_derived")
    added = int(filled["float_shares"].is_not_null().sum() - df["float_shares"].is_not_null().sum())
    if added:
        logger.info("维表股本反推: %d 只标的从 enriched 换手率补齐 float_shares/total_shares", added)
    return filled

def load_name_history(data_dir: Path) -> pl.DataFrame:
    """读取名称变更历史 (symbol, name, as_of); 未生成时返回空表。"""
    path = data_dir / "instrument_name_history" / "part.parquet"
    if not path.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("名称变更历史读取失败: %s", e)
        return pl.DataFrame()


def _local_first_trade_dates(data_dir: Path) -> dict[str, date]:
    """本地日K 每标的的首个交易日 (上市日的近似)。

    全新部署 (目录已建但一个分区都没有) 是正常状态, 不是错误: DuckDB 的
    read_parquet 对空匹配会抛 IOException, 这里直接返回空表, 让上市日回填
    跳过, 不阻断维表同步与整条盘后管道。
    """
    daily_dir = data_dir / "kline_daily"
    if not daily_dir.exists() or not any(daily_dir.glob("date=*")):
        return {}
    import duckdb

    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT symbol, min(CAST(\"date\" AS DATE)) FROM "
            f"read_parquet('{daily_dir}/**/*.parquet', union_by_name=true) "
            "GROUP BY symbol"
        ).fetchall()
    finally:
        con.close()
    return {sym: day for sym, day in rows if sym and day is not None}


def _backfill_listing_dates(df: pl.DataFrame, data_dir: Path) -> pl.DataFrame:
    """provider 未给上市日时, 用本地日K首日回填 (新股)。

    easytdx 上游不提供上市日, 缺失时「注册制新股上市初期无涨跌幅限制」窗口
    判不出来, 上市头几天会被误判成涨跌停。

    回填前提: 本地日K首日就是上市日。本地库实测 387 只有已知上市日的标的
    (首日 >= 注册制起点) 全部满足「首日 == 上市日」, 零反例; 反过来老股的
    首日是同步起始日, 拿它当上市日会让老股凭空多出 5 日窗口。因此只回填
    首日落在注册制起点之后、且该首日不拥挤 (同首日标的数 < 阈值) 的标的 ——
    拥挤日正是历史窗口批量落库的特征。
    """
    if "listing_date" not in df.columns:
        return df
    if df.filter(pl.col("listing_date").is_null()).is_empty():
        return df
    first_dates = _local_first_trade_dates(data_dir)
    if not first_dates:
        return df
    per_day = Counter(first_dates.values())
    mapping = {
        sym: day
        for sym, day in first_dates.items()
        if day >= _REGISTRATION_DATE and per_day[day] < _LISTING_BACKFILL_CROWDED_MIN
    }
    if not mapping:
        return df
    filled = pl.DataFrame(
        {"symbol": list(mapping), "listing_date": list(mapping.values())},
        schema={"symbol": pl.Utf8, "listing_date": pl.Date},
    )
    # listing_date 在维表里是字符串列 (provider 原始形态), 回填值须同类型,
    # 否则 coalesce 会因 String/Date 混合报错。
    target_dtype = df.schema["listing_date"]
    if target_dtype == pl.Null:
        target_dtype = pl.Date
    filled = filled.with_columns(pl.col("listing_date").cast(target_dtype))
    merged = df.join(filled, on="symbol", how="left", suffix="_backfill")
    result = merged.with_columns(
        pl.coalesce(pl.col("listing_date"), pl.col("listing_date_backfill"))
        .alias("listing_date")
    ).drop("listing_date_backfill")
    restored = result.filter(pl.col("listing_date").is_not_null()).height
    logger.info(
        "instruments 上市日回填 %d/%d (本地日K首日, 非拥挤日)",
        restored, df.height,
    )
    return result


def _update_name_history(df: pl.DataFrame, data_dir: Path, as_of: date) -> int:
    """记录名称变更 → data/instrument_name_history/part.parquet。

    维表是同步当日的快照, 单靠它无法还原「某个历史交易日当时叫什么」——
    风险警示标记(简称含 ST)因此会被套用到全历史。名称变更历史按 as_of 记录
    改名时点, 供计算路径逐日还原当日简称。

    只在名称变化时追加 (含首次出现的标的), 避免每天重复写入全量。
    """
    path = data_dir / "instrument_name_history" / "part.parquet"
    current = (
        df.select("symbol", "name")
        .unique(subset=["symbol"])
        .filter(pl.col("name").is_not_null())
    )
    if current.is_empty():
        return 0
    old = pl.DataFrame()
    if path.exists():
        try:
            old = pl.read_parquet(path)
        except Exception as e:  # noqa: BLE001
            logger.warning("名称变更历史读取失败, 重建: %s", e)
            old = pl.DataFrame()
    if old.is_empty():
        changed = current
    else:
        latest = (
            old.sort("as_of")
            .group_by("symbol", maintain_order=True)
            .last()
            .select("symbol", pl.col("name").alias("_last_name"))
        )
        joined = current.join(latest, on="symbol", how="left")
        changed = joined.filter(
            pl.col("_last_name").is_null() | (pl.col("name") != pl.col("_last_name"))
        ).select("symbol", "name")
    if changed.is_empty():
        return 0
    new_rows = changed.with_columns(pl.lit(as_of).alias("as_of"))
    merged = (
        pl.concat([old, new_rows], how="diagonal_relaxed")
        if not old.is_empty()
        else new_rows
    )
    merged = merged.sort(["symbol", "as_of"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(merged, path)
    return changed.height


def enrich_names_from_quotes(
    data_dir: Path,
    quotes_data: list[dict],
) -> int:
    """从 quotes 响应中提取 name，更新 instruments 维表（兜底补充）。

    盘后 quotes.get(universes) 返回的数据中包含 ext.name，
    用来补充 instruments 中可能缺失的 name。
    """
    if not quotes_data:
        return 0

    # 构建 symbol → name 映射
    name_map: dict[str, str] = {}
    for q in quotes_data:
        symbol = q.get("symbol", "")
        ext = q.get("ext") or {}
        name = ext.get("name") or q.get("name", "")
        if symbol and name:
            name_map[symbol] = name

    if not name_map:
        return 0

    inst_path = data_dir / "instruments" / "instruments.parquet"
    if not inst_path.exists():
        return 0

    df = pl.read_parquet(inst_path)

    # 只更新空 name 的行
    updates = pl.DataFrame({
        "symbol": list(name_map.keys()),
        "_new_name": list(name_map.values()),
    })
    df = df.join(updates, on="symbol", how="left")
    df = df.with_columns(
        pl.when(pl.col("name").is_null() | (pl.col("name") == ""))
        .then(pl.col("_new_name"))
        .otherwise(pl.col("name"))
        .alias("name"),
    ).drop("_new_name")

    atomic_write_parquet(df, inst_path)
    logger.info("instruments name enriched from quotes: %d names", len(name_map))
    return len(name_map)
