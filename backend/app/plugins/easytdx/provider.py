"""EasyTdx(easy-tdx-plus)内置数据源 provider。

方法签名对齐 custom.GenericHTTPProvider(service 分流点按这套签名调用),
插件被 loader 扫描注册后, 各 service 无需改动即可路由。

实现数据集: daily / adj_factor / realtime / minute / depth5
未声明 financial / full_minute → provider_has_dataset 为 False, 回退默认源。

★ 通道: 全部行情走 MAC 协议(MacClient), 只有除权事件走标准协议 TdxClient。
  本机实测(2026-11)标准协议的 K 线/行情命令在可用节点上恒返回 2 字节残缺响应,
  而 MAC 协议日K/分钟K/全市场快照/指数全部正常。细节见 client.py 模块 docstring。

单位与口径 (CONTRIBUTING §3, 不可凭字段名推断; 2026-11 实测):
  - 日K/分钟K `vol` 为股 → floor(/100) 转手(茅台 11-06 日K 4284772 股 = 42847 手,
    与同刻快照 vol 42847 对得上); `amount` 为元直通。
  - 快照(全市场列表与逐只报价)`vol` 已为手 → 零换算直通; `amount` 为元。
  - 快照 `turnover` 是百分数(平安银行 0.845255 ≈ 0.85%) → /100 转小数制。
  - 快照 `total_shares`/`float_shares` 为**万股** → ×10000 转股
    (本库 instruments.parquet 里 float_shares 是股, 茅台 1.2501e9)。
  - 日K/分钟K `datetime` 是 naive datetime64[ns](服务端墙钟) → 天然满足
    「北京墙钟 naive」契约, 不做任何时区换算。
  - 取数固定 `Adjust.NONE` 锁定原始价; 前复权由 indicators.pipeline 用本地因子链。
  - 除权事件 `fenhong`/`songzhuangu`/`peigu` 在库内已 ÷10 成每股, **不得再除 10**。
  - ex_factor 为单事件比值(非累积), 公式与自检走共享模块 ex_factor.py。

覆盖: 沪深京 A 股 + 指数 + ETF(全市场列表只取 Category.A; ETF 走默认 null 计数)。
"""

from __future__ import annotations

import importlib.util
import logging
import math
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TypeVar

import polars as pl

from app.data_providers.ex_factor import factor_rows
from app.plugins.easytdx.client import EasyTdxClient, EasyTdxError, parse_symbol

logger = logging.getLogger(__name__)

_DATASETS = ("daily", "adj_factor", "realtime", "minute", "depth5", "full_minute")

_QUOTE_BATCH = 80  # 上游逐只报价单次 ≤80 只
_HIST_INTERVAL_S = 0.02  # 单标的 K 线/事件请求节流(本地 TCP, 比 REST 源轻)
_ADJ_BACKDAYS = 30  # 推导因子时向前找"除权日前收盘"的回看天数(容忍长期停牌)
_ADJ_FUTURE_DAYS = 15  # 未来已公告事件窗口(无前收盘自然跳过)
_DAILY_MAX_COUNT = 6000  # ≈24 年; 服务端深度不足时在空页自动终止
_SYNC_WORKERS = 16  # 逐标的并发路数(实测 256 只: 单路 91ms/只 → 16 路 9ms/只)
_DAILY_FRAME_SYMBOLS = 500  # 每帧聚合的标的数 → 落盘小文件数 = 帧数 × 天数
_MINUTE_MAX_COUNT = 9600  # 服务端 1m 实测保留约 21 个交易日
_MINUTE_BARS_PER_DAY = 240
_MINUTE_HISTORY_DAYS = 20  # 实测 21 个交易日, 向下取整声明

_DAILY_SCHEMA = {
    "symbol": pl.String, "date": pl.Date,
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Int64, "amount": pl.Float64,
}
_ADJ_SCHEMA = {
    "symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64,
    "dividend": pl.Float64, "bonus": pl.Float64, "allot": pl.Float64,
    "allot_price": pl.Float64, "prev_close": pl.Float64,
}
_MINUTE_SCHEMA = {
    "symbol": pl.String, "datetime": pl.Datetime("us"),
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Float64, "amount": pl.Float64,
}

# 内部指数代码 → easy_tdx 报价代码(实测 000001.SH 直通即为上证指数, 无需别名)
_INDEX_CODE_MAP: dict[str, str] = {}


def _module_available() -> bool:
    try:
        return importlib.util.find_spec("easy_tdx") is not None
    except Exception:  # noqa: BLE001 — find_spec 对损坏安装也会抛
        return False


def availability() -> tuple[bool, str]:
    """loader 启动自检: 只查模块是否装好, 不做网络探测(冷启动不该等 TCP 超时)。"""
    if not _module_available():
        return False, "easy-tdx-plus 未安装, 点击「安装依赖」或 uv sync --extra easytdx"
    return True, "ok"


@dataclass
class _EasyTdxConfig:
    """轻量 config shim, 让 custom loader 的 provider_has_dataset 能识别本 provider。"""

    name: str = "easytdx"
    display_name: str = "通达信直连"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _to_int_lots(shares) -> int | None:
    """股 → 手(向下取整)。日K/分钟K 的 vol 是股, 快照的 vol 已在手, 两者不可混用。"""
    v = _to_float(shares)
    return math.floor(v / 100.0) if v is not None else None


def _as_date(value) -> date | None:
    """pd.Timestamp / datetime / 'YYYY-MM-DD' / 'YYYYMMDD' → date; 非法返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            text = text[:10].replace("-", "")
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except (TypeError, ValueError):
        return None


def _as_naive_dt(value) -> datetime | None:
    """K线 datetime → 北京墙钟 naive。值是 naive 时原样用(服务端即墙钟, 不换算)。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    try:
        import pandas as pd  # easy_tdx 的返回只有 pandas; 仅解析期需要

        ts = pd.Timestamp(value)
        return None if ts is pd.NaT else ts.to_pydatetime().replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def _bars_for_window(span_days: int) -> int:
    """窗口自然日跨度 → count 模式请求根数(交易日≈自然日×5/7, 加停牌冗余)。"""
    return max(30, min(_DAILY_MAX_COUNT, int(span_days * 5 / 7) + 20))


def _fork_client(client: EasyTdxClient) -> EasyTdxClient:
    """并发拉取要独立连接: 能 fork 就另开一条, 否则复用(共用会被锁串行化)。"""
    fork = getattr(client, "fork", None)
    return fork() if callable(fork) else client


_T = TypeVar("_T")


def _map_parallel(
    client: EasyTdxClient,
    symbols: list[str],
    fn: Callable[[EasyTdxClient, str], _T],
    *,
    workers: int = _SYNC_WORKERS,
    on_progress: Callable[[int, int], None] | None = None,
) -> Iterator[tuple[str, _T]]:
    """逐标的并发拉取: 每路 worker 一条独立连接, 结果按 symbols 原序产出。

    - **fn 自行决定软/硬失败**: 抛异常 → 整轮中断, 异常原样上抛(调用方语义不变);
    - 共用一条连接会被 EasyTdxClient 的 RLock 重新串行化, 故每路 fork() 一条;
    - 先 prepare 一次: from_best_host 要 ping 全部节点, 各路分叉只做建连;
    - 结果按输入序产出(落盘顺序稳定), 内存只滞留 in-flight 的少量结果。

    ponytail: 路数按本地 TCP 实测定(256 只 91ms/只 → 16 路 9ms/只); 节点限流变严时调小
    _SYNC_WORKERS。
    """
    if not symbols:
        return
    total = len(symbols)
    workers = max(1, min(workers, total))
    prepare = getattr(client, "prepare", None)
    if callable(prepare):
        prepare()  # 先选优一次主机, 各路分叉只建连(选优要 ping 全部节点)
    shards = [list(range(i, total, workers)) for i in range(workers)]

    out_q: queue.Queue = queue.Queue(maxsize=workers * 2)
    stop = threading.Event()

    def _put(item) -> bool:
        """带停止检查的入队: 队列满时轮询, 消费端提前退出则放弃投递。"""
        while not stop.is_set():
            try:
                out_q.put(item, timeout=0.5)
                return True
            except queue.Full:
                continue
        return False

    def _run(shard: list[int], index: int) -> None:
        conn = client
        if index:  # 首路用主连接, 其余各自分叉(在线程内并行建连)
            try:
                conn = _fork_client(client)
            except Exception as e:  # noqa: BLE001 — 分叉失败退回单连接, 只是变慢
                logger.warning("EasyTdx 并发连接分叉失败, 退回单连接: %s", e)
        try:
            for pos in shard:
                if stop.is_set():
                    break
                if not _put((pos, fn(conn, symbols[pos]))):
                    return
                time.sleep(_HIST_INTERVAL_S)
        except BaseException as exc:  # noqa: BLE001 — 交消费线程原样重抛
            _put(exc)
        finally:
            if conn is not client:
                conn.close()
            _put(None)  # 哨兵: 每路 worker 必发, 否则消费端会等空

    threads = [
        threading.Thread(target=_run, args=(shards[i], i),
                         name=f"easytdx-sync-{i}", daemon=True)
        for i in range(workers)
    ]
    try:
        for t in threads:
            t.start()
        pending: dict[int, _T] = {}
        next_pos = 0
        done = 0
        alive = workers
        while alive:
            item = out_q.get()
            if item is None:
                alive -= 1
                continue
            if isinstance(item, BaseException):
                raise item
            pos, value = item
            pending[pos] = value
            while next_pos in pending:  # 按输入序吐结果(完成顺序乱序到达)
                yield symbols[next_pos], pending.pop(next_pos)
                next_pos += 1
                done += 1
                if on_progress:
                    on_progress(done, total)
    finally:
        stop.set()
        for t in threads:
            if t.ident is not None:
                t.join(timeout=5)


def _minute_count_for_window(start_dt: datetime | None, end_dt: datetime) -> int:
    span = (end_dt - start_dt).days if start_dt else _MINUTE_HISTORY_DAYS
    return max(_MINUTE_BARS_PER_DAY, min(_MINUTE_MAX_COUNT, (int(span) + 1) * _MINUTE_BARS_PER_DAY + 300))


def _minute_frame(
    symbol: str,
    bars: list[dict],
    start_dt: datetime | None,
    end_dt: datetime | None,
) -> pl.DataFrame:
    """分钟K 原始行 → 契约帧。get_minute 与 get_intraday_batch 共用同一映射。

    单位: vol 股→手(÷100), amount 元直通; datetime 已是北京墙钟 naive。
    """
    rows = []
    for bar in bars:
        dt = _as_naive_dt(bar.get("datetime"))
        if dt is None or (end_dt is not None and dt > end_dt) or (start_dt is not None and dt < start_dt):
            continue
        vol = bar.get("vol")
        rows.append({
            "symbol": symbol, "datetime": dt,
            "open": _to_float(bar.get("open")), "high": _to_float(bar.get("high")),
            "low": _to_float(bar.get("low")), "close": _to_float(bar.get("close")),
            "volume": _to_float(vol) / 100.0 if vol is not None else None,
            "amount": _to_float(bar.get("amount")),
        })
    return pl.DataFrame(rows, schema=_MINUTE_SCHEMA)


def _symbol_of(market, code) -> str | None:
    """(market, code) → '600519.SH'; market 不认识返回 None(不猜后缀)。"""
    suffix = {1: "SH", 0: "SZ", 2: "BJ"}.get(int(market) if market is not None else -1)
    return f"{code}.{suffix}" if suffix and code else None


def _map_snapshot_row(row: dict, fallback_ms: int) -> dict | None:
    """快照行 → 内部 realtime record。单位: vol 手直通, amount 元, turnover %→小数。"""
    symbol = _symbol_of(row.get("market"), row.get("code"))
    if symbol is None:
        return None
    last = _to_float(row.get("close"))
    prev = _to_float(row.get("pre_close"))
    # 停牌/未上市票的快照 OHLC 全 0(MAC 分类列表不提供 trading_status,
    # 只能凭价判断) → 会算出 -100% 的假跌停, 视为无行情剔除
    if last is None or last <= 0:
        return None
    change_amount = last - prev if (last is not None and prev is not None) else None
    # 与 quote_service 推导同口径: 小数制, 不乘 100
    change_pct = change_amount / prev if (change_amount is not None and prev) else None
    volume = _to_float(row.get("vol"))  # 快照已为手, 零换算
    turnover = _to_float(row.get("turnover"))  # 百分数 → 小数制
    return {
        "symbol": symbol,
        "name": row.get("name") or None,
        "last_price": last,
        "prev_close": prev,
        "open": _to_float(row.get("open")),
        "high": _to_float(row.get("high")),
        "low": _to_float(row.get("low")),
        "volume": math.floor(volume) if volume is not None else None,
        "amount": _to_float(row.get("amount")),
        "change_pct": change_pct,
        "change_amount": change_amount,
        "amplitude": None,  # 快照未提供, 不启发式计算
        "turnover_rate": turnover / 100.0 if turnover is not None else None,
        "timestamp": fallback_ms,  # MAC 报价不带服务端时间, 退本地(契约允许)
        "session": None,
    }


class EasyTdxProvider:
    """easy-tdx-plus 数据源。realtime = 全市场分类列表一次拉取; depth5 = 逐只报价买一/卖一。"""

    name = "easytdx"
    builtin = True
    minute_history_days = _MINUTE_HISTORY_DAYS

    def __init__(self) -> None:
        self.config = _EasyTdxConfig()
        self._client: EasyTdxClient | None = None

    def close(self) -> None:  # loader.load_all 重建注册表时会对每个 provider 调 close
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> EasyTdxClient:
        if self._client is None:
            self._client = EasyTdxClient()
        return self._client

    # ---- realtime ----
    def get_realtime(self) -> list[dict]:
        """全市场实时快照(实测 5580 只 / 7.5 秒, 含北交所)。软失败返回 []。"""
        fallback_ms = int(time.time() * 1000)
        try:
            rows = self._get_client().quote_list("A", count=6000)
        except EasyTdxError as e:
            logger.warning("EasyTdx 全市场快照失败: %s", e)
            return []
        records = [r for r in (_map_snapshot_row(row, fallback_ms) for row in rows) if r]
        if not records:
            logger.warning("EasyTdx 实时行情本轮 0 条(原始 %d 行), 疑似协议结构变化", len(rows))
        # 全市场分类列表偶发同一标的多行 (实测 5572 条记录 / 5559 个唯一 symbol)。
        # 不去重会在下游按 symbol 的 join 里平方放大, 落盘分区出现重复行,
        # 回测矩阵直接拒绝 (MarketDataMatrix requires unique timestamp/symbol rows)。
        if len(records) > 1:
            deduped = {r["symbol"]: r for r in records}
            if len(deduped) != len(records):
                logger.info("EasyTdx 实时快照去重: %d → %d 条", len(records), len(deduped))
            records = list(deduped.values())
        return records

    def get_realtime_indices(self, symbols: list[str]) -> list[dict] | None:
        """指数实时快照。失败返回 None 保留上轮缓存, 成功但无数据返回 []。"""
        wanted = []
        for s in symbols:
            if not s:
                continue
            code = _INDEX_CODE_MAP.get(s, s)
            if code not in wanted:
                wanted.append(code)
        if not wanted:
            return []
        fallback_ms = int(time.time() * 1000)
        records: list[dict] = []
        try:
            client = self._get_client()
            for i in range(0, len(wanted), _QUOTE_BATCH):
                for row in client.quotes(wanted[i : i + _QUOTE_BATCH]):
                    rec = _map_snapshot_row(row, fallback_ms)
                    if rec is not None:
                        records.append(rec)
        except EasyTdxError as e:
            logger.warning("EasyTdx 指数行情拉取失败: %s", e)
            return None
        return records

    # ---- daily ----
    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        chunks = [
            df
            for df in self.iter_daily(
                symbols, start_time=start_time, end_time=end_time,
                asset_type=asset_type, on_chunk_done=on_chunk_done,
            )
            if not df.is_empty()
        ]
        return pl.concat(chunks, how="diagonal_relaxed") if chunks else pl.DataFrame(schema=_DAILY_SCHEMA)

    def iter_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
        on_symbol_failed: Callable[[str, str], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        """并发拉日K(每页 700 根由库内部翻页) → 原始价契约帧。

        并发: _SYNC_WORKERS 路各持一条独立 MAC 连接(共用一条连接会被
        EasyTdxClient 的 RLock 重新串行化)。实测 256 只标的 91ms/只 → 9ms/只。
        聚帧: 每 _DAILY_FRAME_SYMBOLS 只聚成一帧 —— 落盘侧按 (帧 × 日期) 写
        staging 小文件, 逐只一帧会写出「标的数 × 天数」个文件(全市场一年 =
        139 万个), 聚帧后是「帧数 × 天数」。

        on_symbol_failed(symbol, reason): 可选回调, 单票拉取失败时触发 ——
        上层据此把单票失败上浮为阶段错误, 不与「窗口内无数据」混为一谈。
        """
        if not symbols:
            if on_chunk_done:
                on_chunk_done(0, 0)
            return
        start_d = start_time.date() if start_time else None
        end_d = end_time.date() if end_time else date.today()
        span = (end_d - start_d).days if start_d else None
        count = _bars_for_window(int(span)) if span is not None else _DAILY_MAX_COUNT

        client = self._get_client()

        def _fetch(conn: EasyTdxClient, symbol: str) -> pl.DataFrame:
            try:
                bars = conn.kline(symbol, period_name="DAILY", count=count)
            except EasyTdxError as e:
                logger.warning("EasyTdx 日K %s 拉取失败: %s", symbol, e)
                if on_symbol_failed:
                    on_symbol_failed(symbol, str(e))
                bars = []
            rows = self._daily_rows(symbol, bars, start_d, end_d)
            if rows and start_d is not None and rows[0]["date"] > start_d:
                logger.info("EasyTdx 日K %s: count=%d 仅覆盖到 %s(窗口起点 %s 之前不在返回内)",
                            symbol, count, rows[0]["date"], start_d)
            return pl.DataFrame(rows, schema=_DAILY_SCHEMA)

        buf: list[pl.DataFrame] = []
        for _symbol, frame in _map_parallel(client, symbols, _fetch, on_progress=on_chunk_done):
            buf.append(frame)
            if len(buf) >= _DAILY_FRAME_SYMBOLS:
                yield pl.concat(buf, how="vertical")
                buf = []
        if buf:
            yield pl.concat(buf, how="vertical")

    @staticmethod
    def _daily_rows(symbol: str, bars: list[dict], start_d: date | None, end_d: date) -> list[dict]:
        rows: list[dict] = []
        for bar in bars:
            d = _as_date(bar.get("datetime"))
            if d is None or d > end_d or (start_d is not None and d < start_d):
                continue
            rows.append({
                "symbol": symbol, "date": d,
                "open": _to_float(bar.get("open")), "high": _to_float(bar.get("high")),
                "low": _to_float(bar.get("low")), "close": _to_float(bar.get("close")),
                "volume": _to_int_lots(bar.get("vol")), "amount": _to_float(bar.get("amount")),
            })
        return rows

    # ---- adj_factor ----
    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """除权因子 → 内部契约(单事件比值 + 明细列, 每股口径)。

        数据链: xdxr 事件(库内已 ÷10 成每股) → 同一标的日K配前收 →
        交易所公式 + 涨跌停自检, 公式与自检在 ex_factor.py 共享。

        并发: 除权事件走**标准协议**, 与日K的 MAC 通道是两条线; 各路 worker
        各自持有标准协议 + MAC 连接(配价要拉日K)。
        """
        if not symbols:
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        client = self._get_client()
        start_d = start_time.date() if start_time else None
        end_d = end_time.date() if end_time else date.today()

        # 代码格式非法(缺 .SH/.SZ/.BJ 后缀)是**调用方的数据问题**, 不是数据源不可用:
        # 个别脏 symbol 不该让整轮全市场除权失败。逐个跳过并记账, 真正的拉取失败仍然上抛。
        valid: list[str] = []
        for symbol in symbols:
            try:
                parse_symbol(symbol)
            except EasyTdxError:
                logger.warning("EasyTdx 除权跳过无法解析的标的代码: %r", symbol)
                continue
            valid.append(symbol)
        if not valid:
            return pl.DataFrame(schema=_ADJ_SCHEMA)

        # 标准协议通道先选优一次: from_best_host 会 ping 全部节点, 各路分叉直连
        # config.json 里已选优的 best_host(否则 16 路各 ping 一次, 比串行还慢)。
        prepare_std = getattr(client, "prepare_std", None)
        if callable(prepare_std):
            prepare_std()

        horizon = end_d + timedelta(days=_ADJ_FUTURE_DAYS)

        def _fetch(conn: EasyTdxClient, symbol: str) -> list[dict]:
            # 失败不能当作「该股无除权」; 由任务层标记失败, 整轮不写不完整因子
            events = self._events(conn, symbol)
            events = [
                {**ev, "date": _as_date(ev.get("date"))}
                for ev in events
                if _as_date(ev.get("date")) is not None and _is_xdxr(ev)
            ]
            # 先按目标窗口裁事件, 再决定要不要拉日K。窗口外的历史事件最终会被
            # factor_rows 丢弃, 但若用它们算 first_ex, 每只股票都要按其最早除权日
            # (可追溯到 1990 年) 拉满 6000 根日K —— 全市场同步从 5 分钟变 10 小时。
            in_window = [
                ev for ev in events
                if (start_d is None or ev["date"] >= start_d) and ev["date"] <= horizon
            ]
            if not in_window:
                return []
            first_ex = min(ev["date"] for ev in in_window)
            closes = self._close_map(conn, symbol, first_ex - timedelta(days=_ADJ_BACKDAYS), end_d)
            return factor_rows(
                symbol, self._normalized_events(in_window), closes,
                start_d, end_d, future_days=_ADJ_FUTURE_DAYS,
            )

        def _progress(done: int, _total: int) -> None:
            if on_chunk_done:
                on_chunk_done(done, len(symbols))

        out_rows: list[dict] = []
        for _symbol, rows in _map_parallel(client, valid, _fetch, on_progress=_progress):
            out_rows.extend(rows)
        if not out_rows:
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        return (
            pl.DataFrame(out_rows, schema=_ADJ_SCHEMA)
            .unique(subset=["symbol", "trade_date"], keep="last")
            .sort(["symbol", "trade_date"])
        )

    def _events(self, conn: EasyTdxClient, symbol: str) -> list[dict]:
        return conn.xdxr_events(symbol)

    @staticmethod
    def _normalized_events(events: list[dict]) -> list[dict]:
        """easy_tdx 事件字段 → 共享模块要求的事件结构。

        ★ 库内 `_normalize_per_10_shares` 已把 fenhong/songzhuangu/peigu 由
        「每 10 股」÷10 成每股, 这里**不再除 10**; peigujia 是配股价, 不归一。
        """
        return [
            {
                "date": _as_date(ev.get("date")),
                "dividend": _to_float(ev.get("fenhong")) or 0.0,
                "bonus": _to_float(ev.get("songzhuangu")) or 0.0,
                "allot": _to_float(ev.get("peigu")) or 0.0,
                "allot_price": _to_float(ev.get("peigujia")),
            }
            for ev in events
        ]

    def _close_map(self, client: EasyTdxClient, symbol: str, start_d: date, end_d: date) -> dict[date, float]:
        """一次 count 模式日K → {交易日: 原始收盘价}(推导前收盘与自检共用)。"""
        try:
            bars = client.kline(symbol, period_name="DAILY", count=_bars_for_window((end_d - start_d).days))
        except EasyTdxError as e:
            logger.warning("EasyTdx 除权配价 %s 日K失败: %s", symbol, e)
            raise
        out: dict[date, float] = {}
        for bar in bars:
            d = _as_date(bar.get("datetime"))
            close = _to_float(bar.get("close"))
            if d is not None and close is not None:
                out[d] = close
        return out

    # ---- minute ----
    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """分钟K → 契约帧(datetime 服务端北京墙钟 naive, volume 手, amount 元)。"""
        if not symbols or freq != "1m":
            if on_chunk_done:
                on_chunk_done(0, 0)
            return pl.DataFrame(schema=_MINUTE_SCHEMA)
        client = self._get_client()
        start_dt = start_time.replace(tzinfo=None) if start_time is not None else None
        end_dt = (end_time or datetime.now()).replace(tzinfo=None)
        count = _minute_count_for_window(start_dt, end_dt)

        def _fetch(conn: EasyTdxClient, symbol: str) -> pl.DataFrame:
            try:
                bars = conn.kline(symbol, period_name="MIN_1", count=count)
            except EasyTdxError as e:
                # 请求失败应中止历史同步并标记失败, 不得作为无数据继续遍历全市场
                raise EasyTdxError(f"分钟K {symbol}: {e}") from e
            return _minute_frame(symbol, bars, start_dt, end_dt)

        parts = [
            frame
            for _symbol, frame in _map_parallel(client, symbols, _fetch, on_progress=on_chunk_done)
            if not frame.is_empty()
        ]
        return pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame(schema=_MINUTE_SCHEMA)

    def get_intraday_batch(
        self, symbols: list[str], count: int = 300, asset_type: str = "stock",
    ) -> pl.DataFrame:
        """全量分钟修复轮: 当日窗口逐标的拉取, 只保留今日 1m 行。

        上游无批量分钟端点(MAC 协议 K 线命令单标的一次一请求), 与 tdxquant
        同形: 5574 只单线程约 13 分钟(实测 144ms/只), 由 minute_refresh 以
        修复轮节奏调度。count 模式尾部天然带前一日余量, 按今日过滤掉。
        并发后同量级请求约 1 分钟跑完。
        """
        if not symbols or asset_type != "stock":
            return pl.DataFrame(schema=_MINUTE_SCHEMA)
        client = self._get_client()
        today = datetime.now().replace(tzinfo=None).date()
        start_dt = datetime.combine(today, datetime.min.time())
        # 240 根/日 + count 余量, 上限按服务端保留深度截断
        fetch = min(_MINUTE_MAX_COUNT, _MINUTE_BARS_PER_DAY + max(int(count), 0))

        def _fetch(conn: EasyTdxClient, symbol: str) -> pl.DataFrame | None:
            try:
                bars = conn.kline(symbol, period_name="MIN_1", count=fetch)
            except EasyTdxError:
                # 单标的失败不拖垮整轮(修复轮会重来), 与 realtime 软失败同语义
                return None
            return _minute_frame(symbol, bars, start_dt, None)

        parts = [
            frame
            for _symbol, frame in _map_parallel(client, symbols, _fetch)
            if frame is not None and not frame.is_empty()
        ]
        return pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame(schema=_MINUTE_SCHEMA)

    # ---- depth5 ----
    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        """买一/卖一 → 标准盘口字典。

        ★ 上游只提供买一/卖一(PresetField.QUOTE); HANDICAP/ALL 位图超宽会
        抛 OverflowError。数组均为单元素, 2-5 档不填(不补零) —— 消费端
        depth_service 只用 [0] 索引, 单元素完全够用。
        """
        client = self._get_client()
        out: dict[str, dict] = {}
        fallback_ms = int(time.time() * 1000)
        for i in range(0, len(symbols), _QUOTE_BATCH):
            batch = symbols[i : i + _QUOTE_BATCH]
            try:
                rows = client.quotes(batch, preset="QUOTE")
            except EasyTdxError as e:
                logger.warning("EasyTdx 盘口批次失败(第 %d 批): %s", i // _QUOTE_BATCH + 1, e)
                continue
            for row in rows:
                entry = self._depth_entry(row, fallback_ms)
                if entry is not None:
                    out[entry.pop("symbol")] = entry
        return out

    @staticmethod
    def _depth_entry(row: dict, fallback_ms: int) -> dict | None:
        symbol = _symbol_of(row.get("market"), row.get("code"))
        bid = _to_float(row.get("bid_price"))
        ask = _to_float(row.get("ask_price"))
        if symbol is None or bid is None or ask is None:
            return None
        bid_vol = _to_float(row.get("bid_volume"))
        ask_vol = _to_float(row.get("ask_volume"))
        return {
            "symbol": symbol,
            "bid_prices": [bid],
            "bid_volumes": [math.floor(bid_vol) if bid_vol is not None else 0],
            "ask_prices": [ask],
            "ask_volumes": [math.floor(ask_vol) if ask_vol is not None else 0],
            "timestamp": fallback_ms,  # MAC 报价不带服务端时间, 退本地(契约允许)
        }

    # ---- instruments ----
    # easy_tdx 市场分类: A=沪深京A股, ZS=通达信指数, ETF=场内基金
    _INSTRUMENT_CATEGORIES = {"stock": "A", "index": "ZS", "etf": "ETF"}
    # MAC 的 ZS 分类只给 100 条通达信指数 (含 399001/399006/000300/000905),
    # 上证指数 000001.SH 不在其中 → 补核心指数, 否则指数页缺头条指数。
    _CORE_INDEX_NAMES = {
        "000001.SH": "上证指数",
        "399001.SZ": "深证成指",
        "399006.SZ": "创业板指",
    }

    def get_instruments(self, asset_type: str = "stock") -> list[dict]:
        """标的维表 (canonical Instrument 形状)。

        asset_type: stock=沪深京 A 股全市场 (含北交所), index=通达信指数, etf=场内基金。

        股本单位换算: 上游 total_shares/float_shares 是**万股**, 本库 instruments
        存**股** → ×10000。listing_date/tick_size 上游不提供 → None(不伪造)。
        """
        category = self._INSTRUMENT_CATEGORIES.get(asset_type)
        if category is None:
            return []
        rows = self._get_client().quote_list(category, count=6000)
        out: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            symbol = _symbol_of(row.get("market"), row.get("code"))
            if symbol is None or symbol in seen:
                continue
            seen.add(symbol)
            out.append(self._instrument_row(symbol, row, asset_type))
        if asset_type == "index":
            for symbol, name in self._CORE_INDEX_NAMES.items():
                if symbol not in seen:
                    seen.add(symbol)
                    out.append(self._instrument_row(symbol, {"name": name}, asset_type))
        return out

    def resolve_symbols(self, codes: list[str]) -> dict[str, str]:
        """裸代码 → 带交易所后缀的完整代码 (权威: 标准协议全市场证券列表)。

        日K 同步的裸符号 (自选里手打的 600519) 用它补后缀, 不靠代码前缀猜:
        市场里不存在的代码不返回 → 调用方显式跳过, 不带着无效代码发请求。
        代码表本身取不到 → 抛 EasyTdxError (与"代码不存在"是两回事, 调用方
        据此降级, 不能把取不到当成不存在)。
        """
        want = {str(c) for c in codes if c and "." not in str(c)}
        if not want:
            return {}
        rows = self._get_client().security_list()
        out: dict[str, str] = {}
        for row in rows:
            code = str(row.get("code") or "")
            if code not in want:
                continue
            symbol = _symbol_of(row.get("market"), code)
            if symbol:
                out.setdefault(code, symbol)
        return out

    @staticmethod
    def _instrument_row(symbol: str, row: dict, asset_type: str) -> dict:
        total = _to_float(row.get("total_shares"))
        float_shares = _to_float(row.get("float_shares"))
        return {
            "symbol": symbol,
            "name": row.get("name") or None,
            "code": symbol.split(".")[0],
            "exchange": symbol.rsplit(".", 1)[-1],
            "region": "CN",
            "type": asset_type,
            "ext": {
                "listing_date": None,
                "total_shares": total * 10_000.0 if total else None,
                "float_shares": float_shares * 10_000.0 if float_shares else None,
                "tick_size": None,
                "limit_up": _to_float(row.get("buy_price_limit")),
                "limit_down": _to_float(row.get("sell_price_limit")),
            },
        }

    # ---- 设置页试拉 ----
    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset in ("daily", "adj_factor"):
            syms = [s for s in (symbols or [])][:3] or ["600519.SH"]
            try:
                if dataset == "daily":
                    df = self.get_daily(syms, datetime.now() - timedelta(days=30), datetime.now())
                else:
                    df = self.get_adj_factors(syms, datetime.now() - timedelta(days=365), datetime.now())
            except EasyTdxError as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            return self._preview(dataset, df)
        if dataset == "minute":
            syms = [s for s in (symbols or [])][:3] or ["600519.SH"]
            try:
                end = datetime.now()
                df = self.get_minute(syms, end - timedelta(days=3), end)
            except EasyTdxError as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            return self._preview(dataset, df)
        if dataset == "full_minute":
            syms = [s for s in (symbols or [])][:3] or ["600519.SH"]
            try:
                df = self.get_intraday_batch(syms, count=3)
            except EasyTdxError as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            return self._preview(dataset, df)
        if dataset == "realtime":
            recs = self.get_realtime()
            return {"provider": self.name, "dataset": dataset, "rows": len(recs),
                    "columns": list(recs[0].keys()) if recs else [],
                    "preview": recs[:5],
                    **({} if recs else {"error": "本轮快照 0 条, 请检查网络或节点连通性"})}
        if dataset == "depth5":
            syms = [s for s in (symbols or [])][:5] or ["600519.SH", "000001.SZ"]
            try:
                depths = self.get_depth_batch(syms)
            except EasyTdxError as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            return {"provider": self.name, "dataset": dataset, "rows": len(depths),
                    "columns": ["symbol", "bid_prices", "bid_volumes", "ask_prices",
                                "ask_volumes", "timestamp"],
                    "preview": [{"symbol": c, **d} for c, d in list(depths.items())[:5]]}
        return {"provider": self.name, "dataset": dataset, "rows": 0,
                "error": f"easytdx 插件未接入 {dataset} 数据集(自动回退默认源)"}

    def _preview(self, dataset: str, df: pl.DataFrame) -> dict:
        head = df.head(5).to_dicts()
        for row in head:  # date/datetime → ISO 字符串, 保证 JSON 可序列化
            for k, v in list(row.items()):
                if isinstance(v, (date, datetime)):
                    row[k] = v.isoformat()
        return {"provider": self.name, "dataset": dataset, "rows": df.height,
                "columns": df.columns, "preview": head}


def _is_xdxr(ev: dict) -> bool:
    """category==1 为除权除息事件; 其余(送配股说明等)不进因子。"""
    try:
        return int(ev.get("category")) == 1
    except (TypeError, ValueError):
        return False


__all__ = ["EasyTdxProvider", "availability"]