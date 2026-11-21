"""把矩阵策略翻译成可直接拷进 QMT（迅投）运行的单文件脚本。

思路：不翻译策略逻辑，而是把策略的运行环境搬进 QMT —— 内联 ``qmt_shim``
（纯 numpy 复刻 ``app/backtest/matrix.py`` 的算子），再把策略文件 AST 剥掉
类型注解后原样粘贴。策略代码一个字不改，口径因此不会漂移（由
``tests/test_qmt_shim_parity.py`` 与 ``tests/test_qmt_export.py`` 保证）。
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.services import qmt_shim

# 目标环境是 QMT 内嵌的 Python 3.6：没有 dataclass、没有下标式类型注解。
_UNSUPPORTED_BACKENDS = {
    "factor_rank": "截面因子排名策略依赖仓库的因子库与排名管线，QMT 侧无对应数据",
    "polars_expr": "polars 表达式策略依赖 polars，QMT 环境没有该库",
    "python_history_legacy": "旧式 filter_history 策略依赖仓库的指标管线，无矩阵等价物",
    "composite": "叠加策略引用其他策略，请分别导出各子策略",
    "minute_filter": "分钟形态策略依赖当日分钟 K 分区，请用仓库内回测",
}

_ALLOWED_IMPORT_ROOTS = {"numpy"}

_UNKNOWN_STRATEGY = "未实现的策略类型"


class QmtExportError(ValueError):
    """策略无法导出为 QMT 脚本时抛出（API 层转 400）。"""


# ── 策略源码处理 ─────────────────────────────────────────────────
def _strip_module(source: str) -> str:
    """删除仓库内部 import、剥掉所有类型注解，返回可执行源码。"""
    tree = ast.parse(source)
    kept: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_IMPORT_ROOTS:
                continue
        elif isinstance(node, ast.Import):
            if not all(
                alias.name.split(".")[0] in _ALLOWED_IMPORT_ROOTS
                for alias in node.names
            ):
                continue
        kept.append(_strip_node(node))
    tree.body = kept
    return ast.unparse(tree)


def _strip_node(node: ast.AST) -> ast.AST:
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            child.returns = None
            for arg in _all_args(child.args):
                arg.annotation = None
        elif isinstance(child, ast.AnnAssign):
            if child.value is None:
                continue
            # ``x: int = 1`` → ``x = 1``
            child.annotation = None
    return node


def _all_args(arguments: ast.arguments):
    for arg in list(arguments.posonlyargs) + list(arguments.args):
        yield arg
    if arguments.vararg is not None:
        yield arguments.vararg
    for arg in arguments.kwonlyargs:
        yield arg
    if arguments.kwarg is not None:
        yield arguments.kwarg


# ── 驱动层模板 ───────────────────────────────────────────────────
_DRIVER = '''

# ══════════════════════════════════════════════════════════════════
# 以下是 QMT 驱动层，不需要修改
# ══════════════════════════════════════════════════════════════════
_BAR_FIELDS = ["open", "high", "low", "close", "volume", "amount", "preClose"]
_INSTRUMENT_CACHE = {}


# ── 股票池 ────────────────────────────────────────────────────────
def _tradable_codes(C):
    pool = CONFIG["universe"]
    if pool in ("沪深A股", "沪深京A股"):
        codes = list(C.get_stock_list_in_sector("沪深A股"))
        if pool == "沪深京A股":
            codes += list(C.get_stock_list_in_sector("京市A股"))
    else:
        codes = list(C.get_stock_list_in_sector(pool))
    # 剔除可转债 / 逆回购：它们混在A股板块里但语义完全不同
    result = []
    for code in codes:
        code_id = code.split(".")[0]
        if code_id.startswith(("11", "13", "204", "131")):
            continue
        result.append(code)
    if not result:
        # 板块名不存在时 QMT 返回空列表而不报错 —— 空跑比报错更难查
        print("ERROR: 股票池 %r 取到 0 只标的, 请核对 QMT 板块名称"
              % pool)
    return result


def _stock_meta(C, codes):
    """名称 / 流通股本 / 总股本，逐标的缓存。"""
    names = []
    float_shares = []
    total_shares = []
    for code in codes:
        detail = _INSTRUMENT_CACHE.get(code)
        if detail is None:
            detail = C.get_instrument_detail(code, True) or {}
            _INSTRUMENT_CACHE[code] = detail
        names.append(str(detail.get("InstrumentName") or ""))
        float_shares.append(float(detail.get("FloatVolume")
                                  or detail.get("FloatVolumn") or 0.0))
        total_shares.append(float(detail.get("TotalVolume")
                                  or detail.get("TotalVolumn") or 0.0))
    return names, float_shares, total_shares


# ── 取数 ──────────────────────────────────────────────────────────
def _fetch(C, codes, count, end_time, adjust):
    return C.get_market_data_ex(_BAR_FIELDS, codes, period="1d",
                                end_time=end_time, count=count,
                                dividend_type=adjust, fill_data=False,
                                subscribe=not C.do_back_test)


def _panel(raw, codes, field):
    """dict[code -> DataFrame] → (T, N) 矩阵，行轴取并集，停牌日留 NaN。"""
    import pandas as pd

    series = {}
    for code in codes:
        frame = raw.get(code)
        if frame is None or len(frame) == 0 or field not in frame.columns:
            continue
        series[code] = frame[field].astype(float)
    if not series:
        return None, None
    frame = pd.DataFrame({code: series[code] for code in codes if code in series})
    return frame.values.astype(np.float32), [code for code in codes if code in series]


def _build_market(C, codes, count, end_time):
    """取全市场日线，拼成 MarketDataMatrix。"""
    adjusted = _fetch(C, codes, count, end_time, "front_ratio")
    raw_quote = _fetch(C, codes, count, end_time, "none")
    if not adjusted or not raw_quote:
        return None

    open_, symbols = _panel(adjusted, codes, "open")
    close, _ = _panel(adjusted, symbols, "close")
    if open_ is None or close is None:
        return None
    high, _ = _panel(adjusted, symbols, "high")
    low, _ = _panel(adjusted, symbols, "low")
    volume, _ = _panel(adjusted, symbols, "volume")
    raw_close, raw_symbols = _panel(raw_quote, symbols, "close")
    import pandas as pd

    frame = pd.DataFrame(adjusted[symbols[0]]).index
    for code in symbols[1:]:
        frame = frame.union(pd.DataFrame(adjusted[code]).index)
    for code in raw_symbols:
        frame = frame.union(pd.DataFrame(raw_quote[code]).index)
    frame = frame.sort_values()

    def align(raw, field):
        result = np.full((len(frame), len(symbols)), np.nan, dtype=np.float32)
        for column, code in enumerate(symbols):
            data = raw.get(code)
            if data is None or len(data) == 0 or field not in data.columns:
                continue
            row_index = frame.get_indexer(data.index)
            keep = row_index >= 0
            result[row_index[keep], column] = data[field].astype(float).values[keep]
        return result

    open_ = align(adjusted, "open")
    high = align(adjusted, "high")
    low = align(adjusted, "low")
    close = align(adjusted, "close")
    volume = align(adjusted, "volume")
    amount = align(adjusted, "amount")
    raw_close = align(raw_quote, "close")

    names, float_shares, total_shares = _stock_meta(C, symbols)
    float_vector = np.array(float_shares, dtype=np.float32)
    total_vector = np.array(total_shares, dtype=np.float32)

    valid = np.isfinite(close) & np.isfinite(open_) & (close > 0)
    volume = np.where(valid, volume, np.nan).astype(np.float32)

    trade_dates = []
    for stamp in frame:
        digits = "".join(ch for ch in str(stamp) if ch.isdigit())
        trade_dates.append(int(digits[:8]))

    shape = close.shape
    fields = {}
    fields["raw_close"] = raw_close.astype(np.float32)
    fields["float_shares"] = np.broadcast_to(
        float_vector, shape).astype(np.float32)
    fields["total_shares"] = np.broadcast_to(
        total_vector, shape).astype(np.float32)
    turnover = np.full(shape, np.nan, dtype=np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        np.divide(volume * np.float32(10000.0),
                  fields["float_shares"], out=turnover,
                  where=valid & (fields["float_shares"] > 0))
    fields["turnover_rate"] = turnover
    fields["amount"] = amount.astype(np.float32)

    market = MarketDataMatrix(
        open_, high, low, close, volume, valid, None, None, fields,
        symbols, names, trade_dates,
    )
    limits = _price_limit_pct_matrix(market, trade_dates)
    limits[~valid] = np.nan
    fields["price_limit_pct"] = limits
    pre_close = align(raw_quote, "preClose")
    previous = np.full(close.shape, np.nan, dtype=np.float32)
    previous[1:] = raw_close[:-1]
    reference = np.where(np.isfinite(pre_close) & (pre_close > 0),
                         pre_close, previous).astype(np.float32)
    fields["consecutive_limit_ups"] = _consecutive_limit_ups(
        market, reference, raw_close, limits)
    market.tradable = valid
    return market


def _limit_streaks(raw_close, limits_up):
    """涨停连板数：不复权收盘 >= 交易所涨停价即封板，按连续计数。"""
    locked = (np.isfinite(raw_close) & np.isfinite(limits_up)
              & (raw_close > 0) & (raw_close >= limits_up - np.float32(0.005)))
    cumulative = np.cumsum(locked, axis=0, dtype=np.int64)
    zeroed = np.where(locked, 0, cumulative)
    base = np.maximum.accumulate(zeroed, axis=0)
    streak = (cumulative - base).astype(np.float32)
    streak[~np.isfinite(raw_close)] = np.nan
    return locked, streak


def _consecutive_limit_ups(market, pre_close, raw_close, limits):
    """与 indicators.pipeline 同口径：不复权收盘 >= 涨停价即封板。"""
    limits_up = limit_price(pre_close, limits, up=True)
    limits_down = limit_price(pre_close, limits, up=False)
    locked, streak = _limit_streaks(raw_close, limits_up)
    market.limit_up_locked = locked
    market.limit_down_locked = (
        np.isfinite(raw_close) & np.isfinite(limits_down) & (raw_close > 0)
        & (raw_close <= limits_down + np.float32(0.005)))
    return streak


# ── 信号 ──────────────────────────────────────────────────────────
def _signals(market):
    params = dict(PARAMS)
    signals = MATRIX_STRATEGY.compute_signals(market, params)
    basic = dict(BASIC_FILTER or {})
    if not basic.get("enabled", True):
        basic = {}
    mask = build_basic_filter_mask(market, basic) if basic else None
    if mask is None:
        mask = np.ones(market.shape, dtype=bool)
    entry = (signals.entry.astype(bool) & mask).astype(np.uint8)
    score = build_matrix_score(
        market, entry.astype(bool), SCORING, ORDER_BY, DESCENDING,
        fallback=signals.score, directions=SCORING_DIRECTIONS,
    )
    return entry, signals.exit.astype(bool), score


# ── 下单 ──────────────────────────────────────────────────────────
def _account(C):
    return CONFIG.get("account_id") or getattr(C, "account", "") or ""


def _positions(C):
    account = _account(C)
    if not account:
        return {}
    result = {}
    for item in get_trade_detail_data(account, "stock", "position"):
        code = "%s.%s" % (item.m_strInstrumentID, item.m_strExchangeID)
        volume = int(item.m_nCanUseVolume)
        if volume <= 0:
            continue
        result[code] = {
            "volume": volume,
            "cost": float(item.m_dOpenPrice),
        }
    return result


def _available_cash(C):
    account = _account(C)
    if not account:
        return 0.0
    data = get_trade_detail_data(account, "stock", "account")
    if not data:
        return 0.0
    return float(data[0].m_dAvailable) / 100.0


def _lots(price, amount):
    """按金额折手数，取整到 100 股。"""
    if not np.isfinite(price) or price <= 0:
        return 0
    return int(amount / price / 100.0) * 100


def _buy(C, code, price, cash_left):
    volume = _lots(price, min(CONFIG["amount"], cash_left))
    if volume < 100:
        return 0.0
    if not CONFIG["live_trade"]:
        print("[dry-run] BUY %s %d @ %.2f" % (code, volume, price))
    else:
        passorder(23, 1101, _account(C), code, 5, -1, volume,
                  CONFIG["strategy_name"], 2, "qmt-export", C)
    return volume * price


def _sell(C, code, price, volume):
    if not CONFIG["live_trade"]:
        print("[dry-run] SELL %s %d @ %.2f" % (code, volume, price))
    else:
        passorder(24, 1101, _account(C), code, 5, -1, volume,
                  CONFIG["strategy_name"], 2, "qmt-export", C)


def _ranked_entries(C, market, entry_row, score_row):
    """按分数降序返回候选代码。"""
    candidates = []
    for column in range(market.shape[1]):
        if not entry_row[column]:
            continue
        candidates.append((float(score_row[column]), market.symbols[column]))
    candidates.sort(key=lambda item: -item[0])
    return [code for _, code in candidates]


def _held_bars(market, code, today):
    """持仓已过几个交易日；建仓日未知 (重启后旧仓) 返回 None。"""
    since = _STATE.held_since.get(code)
    if since is None:
        return None
    try:
        return market.trading_dates.index(today) - market.trading_dates.index(since)
    except ValueError:
        return None


def _exit_holds(market, exit_row, last_row, positions, today):
    """返回需要卖出的代码 → 原因。"""
    reasons = {}
    for code, info in positions.items():
        if code in market.symbols:
            column = market.symbols.index(code)
            if exit_row[column]:
                reasons[code] = "signal"
                continue
            price = float(last_row[column])
            if np.isfinite(price) and info["cost"] > 0:
                pnl = price / info["cost"] - 1.0
                if STOP_LOSS is not None and pnl <= float(STOP_LOSS):
                    reasons[code] = "stop-loss"
                    continue
        if MAX_HOLD_DAYS is not None:
            held = _held_bars(market, code, today)
            # 建仓日未知时宁可不卖：无脑清仓比漏卖一个止盈更糟
            if held is not None and held >= int(MAX_HOLD_DAYS):
                reasons[code] = "max-hold"
    return reasons


def _trade(C, market, entry_row, exit_row, last_row, today):
    positions = _positions(C)
    held = set(positions)
    cash_left = _available_cash(C)
    if CONFIG["live_trade"] and cash_left <= 0:
        cash_left = 1e12

    for code, reason in _exit_holds(
            market, exit_row, last_row, positions, today).items():
        column = market.symbols.index(code)
        price = float(last_row[column])
        if not np.isfinite(price):
            continue
        _sell(C, code, price, positions[code]["volume"])
        _STATE.held_since.pop(code, None)
        held.discard(code)

    slots = int(CONFIG["max_positions"]) - len(held)
    if slots <= 0:
        return
    for code in _ranked_entries(C, market, entry_row, last_row)[:slots]:
        column = market.symbols.index(code)
        price = float(last_row[column])
        spent = _buy(C, code, price, cash_left)
        if spent > 0:
            _STATE.held_since[code] = today
            cash_left -= spent


def _live_price_row(C, market):
    """实盘用最新分笔价成交；取不到就退回昨收。"""
    row = np.array(market.close[-1], dtype=np.float64, copy=True)
    try:
        ticks = C.get_full_tick(list(market.symbols))
    except Exception:
        return row
    for column, code in enumerate(market.symbols):
        tick = ticks.get(code) or {}
        price = tick.get("lastPrice")
        if price and float(price) > 0:
            row[column] = float(price)
    return row


# ── QMT 入口 ──────────────────────────────────────────────────────
# 注意: 状态必须放模块级对象, ContextInfo 的属性在每根 bar 结束时会回滚到
# 该 bar 开始前的深拷贝 (见 qmt-docs/user_attention.md 与
# question_answer.md「ContextInfo 逐 k 线保存的机制」)。挂在 C 上的缓存
# 会在下一个回调里变成 None, 回测预计算的整张面板会被静默丢弃。
class _State(object):
    def __init__(self):
        self.codes = []
        self.market = None
        self.entry = None
        self.exit = None
        self.last_date = None
        self.held_since = {}

_STATE = _State()


def init(C):
    """init 里禁止取板块与行情 (qmt-docs: 只能拿本地数据, 板块函数不可用)。"""
    if C.do_back_test:
        return
    import datetime as dt
    # CONFIG["trade_time"] 是 strftime 模板, schedule_run 需要具体日期串
    # (yyyymmddHHMMSS) 或 datetime; 直接传模板会 strptime 失败。
    time_point = time.strftime(CONFIG["trade_time"])
    # 每日一次: interval=1 天 + repeat_times=-1 (不限次数)
    C.schedule_run(_on_open, time_point, -1,
                   dt.timedelta(days=1), "qmt-export")


def after_init(C):
    """一次性初始化: 取板块 + 回测预计算。板块函数仅在此与 handlebar 可用。"""
    _STATE.codes = _tradable_codes(C)
    if not C.do_back_test:
        return
    market = _build_market(C, _STATE.codes, -1, "")
    if market is None:
        print("warn: after_init got no market data")
        return
    entry, exit_, score = _signals(market)
    _STATE.market = market
    _STATE.entry = entry
    _STATE.exit = exit_
    print("warmup ok: %d bars x %d symbols" % market.shape)


def handlebar(C):
    """回测逐 K 线：只走当根日期，同一天多次回调只交易一次。"""
    if not C.do_back_test or _STATE.market is None:
        return
    digits = "".join(ch for ch in str(C.get_bar_timetag(C.barpos))
                     if ch.isdigit())
    trade_date = int(digits[:8]) if digits else None
    if trade_date is None or trade_date == _STATE.last_date:
        return
    try:
        row = _STATE.market.trading_dates.index(trade_date)
    except ValueError:
        return
    if row < int(WARMUP_BARS):
        return  # 与仓库回测同口径: 预热区间只喂指标, 不交易
    _STATE.last_date = trade_date
    _trade(C, _STATE.market, _STATE.entry[row], _STATE.exit[row],
           _STATE.market.close[row], trade_date)


def _signal_row(market, entry_fill):
    """该交易哪一行: close_t 允许用今天, open_t+1 必须用今天之前。"""
    today = int(time.strftime("%Y%m%d"))
    last = market.shape[0] - 1
    if entry_fill == "close_t" and int(market.trading_dates[last]) == today:
        return last
    for row in range(last, -1, -1):
        if int(market.trading_dates[row]) < today:
            return row
    return last


def _on_open(C):
    """实盘盘前：重算最近窗口，按上一交易日收盘信号下单。"""
    market = _build_market(C, _STATE.codes, int(WARMUP_BARS) + 5, "")
    if market is None:
        print("warn: no market data at open")
        return
    entry, exit_, score = _signals(market)
    row = _signal_row(market, CONFIG.get("entry_fill", "open_t+1"))
    _trade(C, market, entry[row], exit_[row],
           _live_price_row(C, market), int(market.trading_dates[row]))
'''


# ── 组装 ─────────────────────────────────────────────────────────
def _header(strategy, strategy_id: str, meta: dict) -> str:
    name = meta.get("name") or strategy_id
    description = meta.get("description") or ""
    lines = [
        "#coding:gbk",
        '"""%s — 由 quant-workbench 导出' % name,
        "",
        description,
        "",
        "已知限制（导出快照，仓库改动不会自动同步）:",
        "1. 新股上市初期无涨跌幅窗口未实现，新股信号可能与仓库回测不一致",
        "2. basic_filter 的 exclude_new_days 未生效（仓库在 polars 层过滤，本文件无该口径）",
        "3. ST 判定用 export 时的当期简称，历史 ST 期间会被按板块带宽处理",
        "4. CONFIG['live_trade'] 默认为 False，只打印信号不下单",
        '"""',
        "",
    ]
    return "\n".join(lines)


def _constants(strategy, config: dict, params: dict) -> str:
    meta = strategy.meta
    from app.strategy.scoring import effective_scoring, effective_scoring_directions

    overrides = config.pop("_overrides", None) or {}
    scoring = effective_scoring(meta.get("scoring"), overrides)
    directions = effective_scoring_directions(overrides)
    basic_filter = dict(strategy.basic_filter or {})
    if overrides.get("basic_filter"):
        basic_filter.update(overrides["basic_filter"])

    # 用 repr（Python 字面量）而非 json.dumps —— json 的 true/false/null 在
    # QMT 的 Python 里是未定义名，`PARAMS = {"a": true}` 会 NameError。
    blocks = [
        "PARAMS = %r" % (_literal(params),),
        "CONFIG = %r" % (_literal(config),),
        "BASIC_FILTER = %r" % (_literal(basic_filter),),
        "SCORING = %r" % (_literal(
            {str(k): float(v) for k, v in scoring.items()}),),
        "SCORING_DIRECTIONS = %r" % (_literal(directions),),
        "ORDER_BY = %r" % (_literal(meta.get("order_by")),),
        "DESCENDING = %r" % bool(meta.get("descending", True)),
        "STOP_LOSS = %r" % (strategy.stop_loss
                            if strategy.stop_loss is not None else None),
        "MAX_HOLD_DAYS = %r" % (strategy.max_hold_days
                                if strategy.max_hold_days is not None else None),
        "WARMUP_BARS = %d" % int(config["warmup_bars"]),
    ]
    return "\n".join(blocks)


def _literal(value):
    """执行 eval() 可还原的 Python 字面量 —— 只保留内置容器类型。"""
    if isinstance(value, dict):
        return {_literal(k): _literal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_literal(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return sorted(_literal(item) for item in value)
    return value


def _shim_source() -> str:
    """内联 shim —— 去掉模块 docstring 与 import，避免与文件头重复。"""
    tree = ast.parse(Path(qmt_shim.__file__).read_text(encoding="utf-8"))
    body = []
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr):
            continue  # 模块 docstring
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue  # numpy 由文件头统一 import
        body.append(node)
    tree.body = body
    return ast.unparse(tree)


def _supported(strategy) -> None:
    backend = strategy.execution_backend
    if backend == "matrix_native" and strategy.matrix_strategy is not None:
        return
    if backend in _UNSUPPORTED_BACKENDS:
        raise QmtExportError(
            "%s 策略暂不支持导出为 QMT 脚本：%s"
            % (strategy.meta.get("id", "?"), _UNSUPPORTED_BACKENDS[backend])
        )
    raise QmtExportError(
        "%s 策略的执行后端 %r 不支持导出为 QMT 脚本"
        % (strategy.meta.get("id", "?"), backend)
    )


def build_qmt_script(
    *,
    engine,
    data_dir: Path,
    strategy_id: str,
    config: dict,
) -> tuple[bytes, str]:
    """返回 (GBK 编码的脚本字节, 建议文件名)。"""
    try:
        strategy = engine.get(strategy_id)
    except ValueError as exc:
        raise QmtExportError(str(exc)) from exc
    if strategy.meta.get("research_only"):
        raise QmtExportError("unexported research strategy: %s" % strategy_id)
    _supported(strategy)
    path = strategy.file_path
    if path is None or not path.exists():
        raise QmtExportError("策略源文件不存在: %s" % strategy_id)

    from app.strategy import config as strategy_config

    overrides = strategy_config.load_override(data_dir, strategy_id)
    params = engine.resolve_params(strategy, config.pop("params", None),
                                   overrides)

    config = dict(config)
    config.setdefault("strategy_id", strategy_id)
    config.setdefault("strategy_name", strategy.meta.get("name") or strategy_id)
    config.setdefault("account_id", "")
    config.setdefault("live_trade", False)
    config.setdefault("amount", 10000.0)
    config.setdefault("max_positions", 5)
    config.setdefault("universe", "沪深A股")
    config.setdefault("trade_time", "%Y%m%d093200")
    if int(config.get("warmup_bars") or 0) <= 0:
        # 与 app/backtest/strategy.py 同口径: max(60, 策略声明, 评分字段预热)
        from app.strategy.scoring import effective_scoring, scoring_warmup_bars

        scoring = effective_scoring(strategy.meta.get("scoring"), overrides)
        config["warmup_bars"] = max(
            60,
            int(strategy.matrix_strategy.required_warmup_bars(params)),
            scoring_warmup_bars(scoring),
        )

    body = _strip_module(path.read_text(encoding="utf-8"))

    script = "\n".join([
        _header(strategy, strategy_id, strategy.meta),
        "import time",
        "import numpy as np",
        "",
        _constants(strategy, dict(config, _overrides=overrides), params),
        "",
        "# ── Shim 层：纯 numpy 复刻仓库矩阵算子（勿改） ──",
        _shim_source(),
        "",
        "# ── 策略代码：由 %s 原样导出 ──" % strategy_id,
        body,
        "",
        _DRIVER,
    ])
    # 生成即自检：不只是语法，而是按 qmt-docs 的规范逐条过一遍。
    # 模板改动引入不合规写法时，在这里就炸，不会生成一份在 QMT 里跑不起来的文件。
    from app.services import qmt_lint

    issues = qmt_lint.lint_text(script)
    if issues:
        raise QmtExportError(
            "生成的脚本不符合 QMT 规范:\n"
            + "\n".join("  " + str(i) for i in issues))
    return script.encode("gbk"), "%s.qmt.py" % strategy_id