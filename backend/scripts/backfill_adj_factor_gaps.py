#!/usr/bin/env python
"""用第二源 (扶摇) 补齐/纠正存量除权因子, 前提是事件能被市场佐证。

背景: 除权因子默认走通达信 xdxr 协议。该协议对部分标的只给「转配股上市」类事件
(category 9/15), 没有除权明细 (category 1 的 fenhong/songzhuangu/peigu 全空),
或给的比例与行情矛盾 → 本地算不出除权参考价 → 复权价在除权日凭空出现断层,
该日的涨跌停判定只能作废 (实测 9 只标的 / 9 行)。

流程:
  1. 本地找缺口: 逐行算参考价 (有除权事件用事件参考价, 没有用原始昨收),
     收盘相对参考价的涨跌幅超出板块涨跌幅带 → 该行是疑似缺口;
  2. 本地找错因子: 除权当天是「一字」(开=高=低=收) 但收盘价不是任何一档
     涨跌幅带下的限价 → 一字日必然是涨跌停板, 说明存量因子与行情矛盾;
  3. 只对缺口/错因子标的拉第二源 (扶摇) 的除权事件;
  4. 市场佐证: 第二源参考价推出的涨跌幅带必须覆盖当日最高/最低, 且前收盘价
     与本地一致 —— 覆盖不了就是事件与行情矛盾, 丢弃并报告 (实测主源与第二源
     同时错的 8 行全部在这一步被拦下);
  5. 佐证通过的事件并入存量 (先自动备份), 打印受影响标的 → 需重算 enriched。

另有只报告不改盘的一类: 市场证伪参考价 —— 成交价越过了按存量因子算出的限价
(实测 14 行, 封板 11 行 / 未封板 3 行)。封板行由管线按市场限价兜住 (涨停信号
不丢), 未封板行判定价保持 null。要真正修因子得等权威公告, 两个源的名义送转
比例同样偏, 互相佐证不出来。

写入前自动备份存量 (integrity_backups/adj_factor/all.bak-backfill-<时间戳>.parquet)。
备份不落在数据集目录里: 数据集目录下的散落 parquet 会被 read_parquet 当成分区
数据读进来, 同一 (date, symbol) 出现两次 → 查询多算、矩阵构建失败。可重复执行: 已补齐的行
再次运行不会重复写入 (按 symbol+trade_date 覆盖)。

用法 (从 backend/ 目录运行):
    .venv/bin/python -m scripts.backfill_adj_factor_gaps --dry-run
    .venv/bin/python -m scripts.backfill_adj_factor_gaps
"""
from __future__ import annotations

import argparse
import shutil
from datetime import date, datetime
from pathlib import Path

import polars as pl

from app.config import settings
from app.data_providers.ex_factor import recompute_ex_factor, ref_price
from app.parquet import scan_daily_parquet
from app.plugins.fuyao.provider import FuyaoProvider, get_api_key
from app.price_limits import (
    LIMIT_MOVE_TOLERANCE,
    is_no_limit_day,
    parse_listing_date,
    polars_board_limit_pct,
    price_limit_pct,
)
from app.services.kline_sync import _merge_adj_factor_store

DATA_DIR = Path(settings.data_dir)
STORE = DATA_DIR / "adj_factor" / "all.parquet"

_TICK = 0.005
# 历史涨跌幅带的所有可能档位: 风险警示 5% / 主板 10% / 双创 20% / 北交 30%。
# 风险警示的历史状态不可得 → 只证明「不是任何一档的限价」, 不臆断是哪一档。
_BANDS = (0.05, 0.10, 0.20, 0.30)


def _limit(px: float, band: float, *, up: bool) -> float:
    """整数分算限价 (与交易所一致), 不用 px*(1±band) 的浮点乘法。

    浮点乘法会差 1 分: 300873.SZ 2026-07-01 参考价 23.93 → 浮点 28.716,
    交易所 28.72, 当日最高正好 28.72 —— 浮点算法会把合法行情判成越界。
    """
    numerator = round((1 + band) * 100) if up else round((1 - band) * 100)
    cents = int(px * 100 + 0.5)
    return ((cents * numerator + 50) // 100) / 100


def _limit_hits(ref: float, close: float) -> list[str]:
    """收盘价命中哪几档涨跌幅带的限价 (空 = 哪档都不是 → 与行情矛盾)。"""
    hits = []
    for band in _BANDS:
        if abs(close - _limit(ref, band, up=True)) < 1e-6:
            hits.append(f"+{band:.0%}")
        if abs(close - _limit(ref, band, up=False)) < 1e-6:
            hits.append(f"-{band:.0%}")
    return hits


def _band(symbol: str, day: date) -> float | None:
    """该标的当日的板块涨跌幅带 (不含风险警示 5% 带 —— 历史 ST 状态不可得,
    用板块带做佐证是宽松方向: 只证伪, 不臆断)。"""
    try:
        return price_limit_pct(symbol, day)
    except Exception:  # noqa: BLE001 - 板块判定遇到异常标的时只跳过该行
        return None


def _corroborate(event: dict, prev_close: float | None, day: dict | None) -> tuple[bool, str]:
    """第二源的除权事件是否被市场佐证。

    三道关:
      1. 事件自洽: 参考价可算且为正;
      2. 锚点一致: 第二源的前收盘价与本地前收盘价一致 (否则事件挂错了日子);
      3. 带内交易: 除权参考价推出的涨跌幅带必须覆盖当日最高/最低。
    """
    ref = ref_price(
        event.get("prev_close") or 0.0,
        event.get("dividend") or 0.0,
        event.get("bonus") or 0.0,
        event.get("allot") or 0.0,
        event.get("allot_price") or 0.0,
    )
    if ref is None or ref <= 0:
        return False, "参考价不可算"
    if prev_close is None:
        return False, "本地无前收盘价"
    if abs((event.get("prev_close") or 0.0) - prev_close) > 0.02:
        return False, f"前收不一致 (第二源 {event.get('prev_close')} vs 本地 {prev_close})"
    if day is None:
        return False, "本地无当日行情"
    band = _band(event["symbol"], event["trade_date"])
    if band is None:
        return False, "板块带不可判"
    up, dn = _limit(ref, band, up=True), _limit(ref, band, up=False)
    if day["high"] > up + _TICK:
        return False, f"最高 {day['high']} 越过涨停价 {up}"
    if day["low"] < dn - _TICK:
        return False, f"最低 {day['low']} 跌破跌停价 {dn}"
    return True, f"参考价 {ref}"


def _load_daily() -> pl.DataFrame:
    """本地日K (含原始昨收)。float32 噪声由后面的容差吸收。"""
    return (
        scan_daily_parquet(
            sorted((DATA_DIR / "kline_daily").glob("date=*")),
            cast_options=pl.ScanCastOptions(integer_cast="allow-float"),
        )
        .select(["symbol", "date", "open", "high", "low", "close"])
        .sort(["symbol", "date"])
        .collect(engine="streaming")
        .with_columns(
            pl.col("close").shift(1).over("symbol").alias("_prev_raw"),
            pl.col("date").cast(pl.Date).alias("_d"),
        )
    )


def _wrong_factor_rows(store: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    """本地找「因子与行情矛盾」的行: 除权当天是一字, 但收盘价不是任何一档限价。

    一字 (开=高=低=收) 的日子必然是涨跌停板 —— 否则不存在全天只有一个价。
    存量因子推出的参考价若在 5%/10%/20%/30% 四档带下都对不上这个收盘价,
    说明存量因子错了 (实测本地可验证的 19,363 条事件里只有 1 条命中)。
    """
    ev = store.join(
        daily.select(["symbol", "_d", "open", "high", "low", "close", "_prev_raw"]),
        left_on=["symbol", "trade_date"],
        right_on=["symbol", "_d"],
        how="inner",
    )
    ev = ev.with_columns(
        (
            (pl.col("open") == pl.col("high"))
            & (pl.col("high") == pl.col("low"))
            & (pl.col("low") == pl.col("close"))
        ).alias("_yizi"),
        pl.coalesce([pl.col("prev_close"), pl.col("_prev_raw")]).alias("_pc"),
    )
    ev = ev.filter(pl.col("_yizi") & pl.col("_pc").is_not_null())
    out = []
    for r in ev.iter_rows(named=True):
        ref = r["_pc"] / r["ex_factor"]
        if ref <= 0:
            continue
        if not _limit_hits(ref, r["close"]):
            out.append(
                {
                    "symbol": r["symbol"],
                    "trade_date": r["trade_date"],
                    "_pc": r["_pc"],
                    "ex_factor": r["ex_factor"],
                    "_ref": ref,
                    "close": r["close"],
                }
            )
    return pl.DataFrame(
        out,
        schema={
            "symbol": pl.Utf8,
            "trade_date": pl.Date,
            "_pc": pl.Float64,
            "ex_factor": pl.Float64,
            "_ref": pl.Float64,
            "close": pl.Float64,
        },
    )


def _market_refuted_rows(store: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    """市场证伪参考价的行: 成交价越过了按存量因子算出的限价。

    交易所不允许成交价越过限价。除权日的限价由「因子反推的参考价」算出, 因子
    偏 0.1% (供应商的名义送转比例 vs 交易所实际比例差几个基点) 就让限价差几分
    钱, 于是「收盘封在上限」的真涨停日被算成越界。这类行不是缺口 (事件在),
    是因子偏。

    两类处理:
      - 封板 (收盘 == 最高/最低): 市场自己给出了真限价 → 管线按市场限价兜住,
        涨停/跌停信号不丢;
      - 未封板: 真限价钉不死 (可能还差 1 分, 也可能是行情脏) → 判定价保持
        null, 不猜。
    """
    ev = store.join(
        daily.select(["symbol", "_d", "open", "high", "low", "close", "_prev_raw"]),
        left_on=["symbol", "trade_date"],
        right_on=["symbol", "_d"],
        how="inner",
    ).with_columns(
        pl.coalesce([pl.col("_prev_raw"), pl.col("prev_close")]).alias("_pc")
    ).filter(pl.col("_pc").is_not_null())
    ev = ev.with_columns(
        polars_board_limit_pct(pl.col("symbol"), pl.col("trade_date")).alias("band")
    )
    rows = []
    for r in ev.iter_rows(named=True):
        ref = r["_pc"] / r["ex_factor"]
        band = r["band"]
        if not ref or ref <= 0 or band is None:
            continue
        up, dn = _limit(ref, band, up=True), _limit(ref, band, up=False)
        c, h, lo = r["close"], r["high"], r["low"]
        if c is None or h is None or lo is None:
            continue
        if h <= up + _TICK and lo >= dn - _TICK:
            continue
        rows.append(
            {
                "symbol": r["symbol"],
                "trade_date": r["trade_date"],
                "band": band,
                "ref": round(ref, 4),
                "close": c,
                "high": h,
                "low": lo,
                "limit_up": up,
                "limit_down": dn,
                "side": "up" if h > up + _TICK else "down",
                "sealed": (c == h) if h > up + _TICK else (c == lo),
                "gap": round((h - up) if h > up + _TICK else (dn - lo), 4),
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.Utf8,
            "trade_date": pl.Date,
            "band": pl.Float64,
            "ref": pl.Float64,
            "close": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "limit_up": pl.Float64,
            "limit_down": pl.Float64,
            "side": pl.Utf8,
            "sealed": pl.Boolean,
            "gap": pl.Float64,
        },
    )


def _gap_rows(store: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    """本地找除权缺口: 收盘相对参考价的涨跌幅超出板块带的行。

    排除两类不是缺口的行:
      - 上市初期无涨跌幅窗口 (注册制新股前 5 日) —— 涨跌幅本来就无限制;
      - 维表缺上市日时无法判定窗口 —— 保留, 交给第二源与市场佐证裁决。
    """
    factors = store.select(["symbol", "trade_date", "ex_factor"])
    daily = daily.select(["symbol", "_d", "close", "_prev_raw"]).with_columns(
        (pl.int_range(pl.len()).over("symbol") + 1).alias("_rank"),
    )
    daily = daily.join(
        factors, left_on=["symbol", "_d"], right_on=["symbol", "trade_date"], how="left"
    )
    # 参考价: 有事件 → 前收 / 事件因子 (等于该事件的除权参考价); 无事件 → 原始昨收。
    daily = daily.with_columns(
        pl.when(pl.col("ex_factor").is_not_null() & (pl.col("ex_factor") > 0))
        .then(pl.col("_prev_raw") / pl.col("ex_factor"))
        .otherwise(pl.col("_prev_raw"))
        .alias("_ref")
    )
    band = polars_board_limit_pct(pl.col("symbol"), pl.col("_d"))
    daily = daily.with_columns(
        (
            ((pl.col("close") - pl.col("_ref")) / pl.col("_ref")).abs()
            > band + LIMIT_MOVE_TOLERANCE / pl.col("_ref")
        )
        .fill_null(False)
        .alias("_gap")
    )
    gaps = daily.filter(pl.col("_gap"))
    inst = pl.read_parquet(DATA_DIR / "instruments" / "instruments.parquet").select(
        ["symbol", "listing_date"]
    )
    gaps = gaps.join(inst, on="symbol", how="left")
    keep = []
    for r in gaps.iter_rows(named=True):
        listing = parse_listing_date(r["listing_date"])
        in_window = listing is not None and is_no_limit_day(
            r["symbol"], listing, r["_d"], day_rank=r["_rank"]
        )
        keep.append(not in_window)
    mask = pl.Series(keep, dtype=pl.Boolean)
    gaps = gaps.filter(mask)
    print(
        f"  (原始缺口 {daily.filter(pl.col('_gap')).height} 行 → 排除上市初期无涨跌幅窗口后 {gaps.height} 行)"
    )
    return gaps.select(
        ["symbol", "_d", "_prev_raw", "_ref", "close", "ex_factor", "_rank"]
    )


def _republish(symbols: list[str]) -> int:
    """重算受影响标的的 enriched。

    前复权把历史价格缩放到当前价位, 所以事件日之前的每一行都按新因子变化 ——
    重算起点是该标的的本地历史首日, 不是事件日 (按事件日起算会在起点处留下
    一个假断层)。
    """
    from app.data_providers.repository import DataStore, KlineRepository
    from app.indicators.pipeline import recompute_daily_symbols

    firsts = (
        scan_daily_parquet(sorted((DATA_DIR / "kline_daily").glob("date=*")))
        .filter(pl.col("symbol").is_in(symbols))
        .select(["symbol", pl.col("date").min().over("symbol").alias("first")])
        .collect(engine="streaming")
    )
    if firsts.is_empty():
        return 0
    start = firsts["first"].min()
    print(f"  重算起点 {start} (标的本地历史首日)")
    repo = KlineRepository(DataStore(DATA_DIR))
    return recompute_daily_symbols(repo, "stock", start=start, symbols=sorted(symbols))


def main() -> int:
    parser = argparse.ArgumentParser(description="用第二源补齐除权因子缺口")
    parser.add_argument("--dry-run", action="store_true", help="只报告, 不写盘")
    parser.add_argument("--start", default="2010-01-01", help="第二源事件窗口起点")
    parser.add_argument(
        "--republish",
        default="",
        help="只重算这些标的的 enriched (逗号分隔), 跳过检测与补齐",
    )
    args = parser.parse_args()

    if not STORE.exists():
        print(f"存量不存在: {STORE}")
        return 1

    if args.republish:
        symbols = [s.strip() for s in args.republish.split(",") if s.strip()]
        written = _republish(symbols)
        print(f"重算 {len(symbols)} 标的的 enriched, 写入 {written} 行")
        return 0

    if not get_api_key():
        print("第二源 (扶摇) 未配置 API Key, 无法补齐")
        return 1

    store = pl.read_parquet(STORE)
    print(f"存量: {store.height} 行 / {store['symbol'].n_unique()} 标的")

    daily = _load_daily()
    gaps = _gap_rows(store, daily)
    print(f"本地缺口行: {gaps.height} 行 / {gaps['symbol'].n_unique()} 标的")
    suspects = _wrong_factor_rows(store, daily)
    print(f"因子与行情矛盾 (一字日收盘不是任何限价): {suspects.height} 行")
    refuted = _market_refuted_rows(store, daily)
    print(f"市场证伪参考价 (成交价越过按因子算出的限价): {refuted.height} 行")
    for r in refuted.iter_rows(named=True):
        tag = "封板 → 管线按市场限价兜住" if r["sealed"] else "未封板 → 判定价保持 null"
        print(
            f"  · {r['symbol']} {r['trade_date']}: 参考价 {r['ref']} 涨停价 {r['limit_up']} "
            f"跌停价 {r['limit_down']} 收盘 {r['close']} 高 {r['high']} 低 {r['low']} "
            f"缺口 {r['gap']} ({tag})"
        )
    if not gaps.is_empty():
        print(gaps.select(["symbol", "_d", "_prev_raw", "_ref", "close", "ex_factor"]))
    if not suspects.is_empty():
        print(suspects)
    if gaps.is_empty() and suspects.is_empty():
        print("没有可补齐/可纠正的事件")
        return 0

    # 待修标的 = 缺口标的 ∪ 错因子标的
    targets = sorted(set(gaps["symbol"].unique().to_list()) | set(suspects["symbol"].unique().to_list()))
    provider = FuyaoProvider()
    second = provider.get_adj_factors(
        targets,
        datetime.strptime(args.start, "%Y-%m-%d"),
        datetime(2026, 12, 31),
    )
    if second.is_empty():
        print("第二源无事件")
        return 0
    print(f"\n第二源事件 {second.height} 行 / {second['symbol'].n_unique()} 标的")

    # 待修行 ↔ 第二源事件: 按 (symbol, trade_date) 精确对齐。
    wanted = pl.DataFrame(
        {
            "symbol": gaps["symbol"].to_list() + suspects["symbol"].to_list(),
            "trade_date": gaps["_d"].to_list() + suspects["trade_date"].to_list(),
            "_kind": ["缺事件"] * gaps.height + ["因子错"] * suspects.height,
        }
    )
    joined = wanted.join(second, on=["symbol", "trade_date"], how="left")
    candidates = joined.filter(pl.col("ex_factor").is_not_null())
    no_event = joined.filter(pl.col("ex_factor").is_null())
    print(f"待修行 {wanted.height}, 其中第二源有事件 {candidates.height} 行")

    daily = daily.filter(pl.col("symbol").is_in(targets))

    accepted: list[dict] = []
    rejected: list[tuple[str, str, str]] = []
    for ev in candidates.iter_rows(named=True):
        symbol, day = ev["symbol"], ev["trade_date"]
        same = daily.filter((pl.col("symbol") == symbol) & (pl.col("_d") == day))
        prev = daily.filter((pl.col("symbol") == symbol) & (pl.col("_d") < day)).tail(1)
        ok, reason = _corroborate(
            ev,
            prev["close"][0] if prev.height else None,
            same.row(0, named=True) if same.height else None,
        )
        if ok and ev["_kind"] == "因子错":
            # 错因子行: 新因子必须让「一字」的收盘价落在某一档限价上,
            # 否则等于换了一个同样与行情矛盾的因子。
            ref = ref_price(
                ev.get("prev_close") or 0.0,
                ev.get("dividend") or 0.0,
                ev.get("bonus") or 0.0,
                ev.get("allot") or 0.0,
                ev.get("allot_price") or 0.0,
            )
            hits = _limit_hits(ref, same["close"][0]) if (ref and same.height) else []
            if not hits:
                ok, reason = False, "新因子的参考价仍对不上一字收盘价"
            else:
                reason = f"参考价 {ref} 命中 {'/'.join(hits)}"
        if ok:
            accepted.append(ev)
        else:
            rejected.append((symbol, str(day), reason))

    print(f"\n市场佐证通过: {len(accepted)} 行")
    print(f"佐证不通过 (丢弃): {len(rejected)} 行")
    for symbol, day, reason in rejected:
        print(f"  ✗ {symbol} {day}: {reason}")
    if no_event.height:
        print(f"第二源也没有事件 (真实缺口/停牌重整, 无法补齐): {no_event.height} 行")
        for r in no_event.select(["symbol", "trade_date"]).iter_rows():
            print(f"  ✗ {r[0]} {r[1]}: 两源均无除权事件")

    if not accepted:
        print("没有可补齐的事件")
        return 0

    new_rows = pl.DataFrame(accepted).select(
        [
            "symbol",
            "trade_date",
            "dividend",
            "bonus",
            "allot",
            "allot_price",
            "prev_close",
        ]
    )
    # 因子口径与存量一致: ex_factor = 前收 / 除权参考价 (见 ex_factor.recompute_ex_factor)。
    new_rows = new_rows.with_columns(
        pl.struct(["prev_close", "dividend", "bonus", "allot", "allot_price"])
        .map_elements(
            lambda r: recompute_ex_factor(
                r["prev_close"], r["dividend"], r["bonus"], r["allot"], r["allot_price"]
            ),
            return_dtype=pl.Float64,
        )
        .alias("ex_factor")
    )
    print("\n补齐明细:")
    print(
        new_rows.select(
            ["symbol", "trade_date", "ex_factor", "prev_close", "dividend", "bonus", "allot"]
        )
    )

    if args.dry_run:
        print("(dry-run, 未写盘)")
        return 0

    backup_dir = DATA_DIR / "integrity_backups" / "adj_factor"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"all.bak-backfill-{datetime.now():%Y%m%d%H%M%S}.parquet"
    shutil.copy2(STORE, backup)
    print(f"\n已备份存量 → {backup}")
    rows, changed = _merge_adj_factor_store(STORE, new_rows)
    print(f"存量写入: 新增 {rows} 行, 因子变化涉及 {len(changed)} 标的")
    print("受影响标的: " + "、".join(changed))
    written = _republish(changed)
    print(f"重算 enriched: 写入 {written} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
