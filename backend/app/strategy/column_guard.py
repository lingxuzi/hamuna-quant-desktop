"""策略源码静态校验 — 引用的列必须真实存在, 后端与算子必须匹配。

背景: AI 生成的 polars_expr 策略常在 filter() 里引用从未物化的辅助列
(`pl.col("_ma5")`) 或 enriched 中不存在的指标列 (`pl.col("ma100")`),
运行期才抛 ColumnNotFoundError → API 500 + 用户只看到一串英文异常。
这里在生成/保存期用 AST 静态拦下, 让模型自修复或给出可读错误。

fail-open: 动态列名 / pl.all() / cs.* 等不可证明形态一律跳过检查,
宁可不拦, 也不误杀合法策略。
"""
from __future__ import annotations

import ast
import logging

from app.indicators.pipeline import ENRICHED_COLUMNS, ENRICHED_STORAGE_COLS
from app.strategy.feature_deps import collect_referenced_columns
from app.strategy.scoring import VIRTUAL_SCORING_DEPENDENCIES

logger = logging.getLogger(__name__)

# 信号列前缀: 内置种子 signal_* / 用户自定义 csg_* / 盘中 csgi_*。
# csg_/csgi_ 是用户数据, 静态不可知 → 按前缀放行 (运行期由引擎校验缺列)。
_SIGNAL_PREFIXES = ("csg_", "csgi_")

# signal_ 前缀曾整体放行, 导致模型编造 signal_ma_golden_5_60 这类不存在的列
# 也能过校验; filter() 里那条件永远是 False, 策略静默退化成「买全市场」
# (回测照跑不报错)。现改为查已注册信号表, 不存在就报错让模型重写。
_BUILTIN_SIGNAL_PREFIX = "signal_"

# polars_expr 的 filter() 只拿到「当日一行一标的」快照: 跨日算子取不到值,
# 而回测路径喂整段面板 → 两条路径口径不一致。此类算子一律禁用。
_WINDOW_OPS = frozenset({
    "shift", "diff", "pct_change",
    "cum_sum", "cum_prod", "cum_max", "cum_min",
    "ewm_mean", "ewm_sum",
})
_ROLLING_OPS = frozenset({
    "rolling_mean", "rolling_sum", "rolling_std", "rolling_var",
    "rolling_min", "rolling_max", "rolling_median", "rolling_quantile",
})

# 分钟帧列 (minute_filter 输入)
_MINUTE_COLUMNS = frozenset({
    "symbol", "datetime", "open", "high", "low", "close", "volume", "amount",
})


def _registered_signal_columns() -> frozenset[str]:
    """当前真实注册的信号列名 (内置种子 + 用户自定义)。

    注册表未就绪时返回空集 → 所有 signal_* 都被判为不存在。这是有意的
    fail-closed: 宁可让生成期报错重写, 也不放行一个运行期才炸的拼写。
    """
    try:
        from app.indicators.pipeline import _get_custom_signal_exprs

        return frozenset(_get_custom_signal_exprs())
    except Exception as exc:  # noqa: BLE001 — 注册表不可用时按空集处理
        logger.debug("column_guard: 信号注册表不可用: %s", exc)
        return frozenset()


def _allowed_columns(backend: str, meta: dict) -> frozenset[str]:
    """该后端运行时帧里真实存在的列。"""
    if backend == "minute_filter":
        return _MINUTE_COLUMNS
    columns = set(ENRICHED_COLUMNS) | set(ENRICHED_STORAGE_COLS)
    # 虚拟评分字段只有进了 META.scoring 才会被物化到帧上
    scoring = meta.get("scoring") if isinstance(meta.get("scoring"), dict) else {}
    columns |= set(VIRTUAL_SCORING_DEPENDENCIES) & {str(key) for key in scoring}
    try:
        from app.strategy.custom_signals import allowed_fields

        columns |= set(allowed_fields())
    except Exception as exc:  # noqa: BLE001 — 因子注册表未就绪时按基础集放行
        logger.debug("column_guard: allowed_fields 不可用: %s", exc)
    return frozenset(columns)


def _local_columns(tree: ast.AST) -> set[str]:
    """策略自己造出来的列 (alias / agg 命名参数) — 只有 filter_history 能物化。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "alias":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.add(arg.value)
        elif node.func.attr == "agg":
            names.update(kw.arg for kw in node.keywords if kw.arg)
    return names


def _window_ops(tree: ast.AST) -> set[str]:
    ops: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in _WINDOW_OPS or node.func.attr in _ROLLING_OPS:
            ops.add(node.func.attr)
    return ops


def _ungrouped_window_ops(tree: ast.AST) -> set[str]:
    """跨日算子未按标的切窗: 多标的帧上会把上一个标的的值串到下一个标的。

    `pl.col("close").rolling_mean(100)` 在「多标的 × 多日」帧上是全帧滚动 →
    静默算错 (不报错, 这才是最坏形态)。必须 `.over("symbol")` 或先 group_by。
    出现 group_by/agg 时无法静态判定分组口径 → 整体跳过, 宁可不拦。
    """
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"group_by", "agg", "partition_by"}
        for node in ast.walk(tree)
    ):
        return set()
    covered: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "over":
            # 整个 over(...) 子树都算按标的切窗 (含链式 .over().shift() 的前半段)。
            covered.update(id(inner) for inner in ast.walk(node))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (node.func.attr in _WINDOW_OPS or node.func.attr in _ROLLING_OPS)
        and id(node) not in covered
    }


def _int_value(node: ast.expr | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return int(node.value)
    return None


def _param_bound(meta: dict, name: str) -> int | None:
    """META.params 里某个 id 的上界 (取 max, 没有则取 default)。"""
    for item in meta.get("params") or []:
        if not isinstance(item, dict) or str(item.get("id") or "") != name:
            continue
        for key in ("max", "default"):
            value = item.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return int(value)
    return None


def _required_window(tree: ast.AST, meta: dict) -> int | None:
    """filter_history 内用到的最大滚动窗口 (交易日); 不可证明时返回 None。"""
    sizes: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _ROLLING_OPS:
            continue
        size_node = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg == "window_size"), None
        )
        if size_node is None:
            return None
        size = _int_value(size_node)
        if size is None and isinstance(size_node, ast.Name):
            size = _param_bound(meta, size_node.id)
        if size is None:
            return None
        sizes.append(size)
    return max(sizes) if sizes else None


def _declared_lookback(tree: ast.Module, meta: dict) -> int | None:
    """策略声明的历史窗口 (LOOKBACK_DAYS / META.lookback_days / lookback_days 参数)。"""
    values: list[int] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "LOOKBACK_DAYS":
                size = _int_value(node.value)
                if size is not None:
                    values.append(size)
    for item in meta.get("params") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == "lookback_days":
            size = item.get("default")
            if isinstance(size, int) and not isinstance(size, bool):
                values.append(int(size))
    meta_size = meta.get("lookback_days")
    if isinstance(meta_size, int) and not isinstance(meta_size, bool):
        values.append(int(meta_size))
    return max(values) if values else None


def check_strategy_source(code: str, backend: str, meta: dict | None = None) -> list[str]:
    """静态检查策略源码; 返回问题描述列表 (空 = 通过)。"""
    meta = meta or {}
    if backend == "matrix_native":
        return []  # 矩阵策略走 market 协议, 不引用 pl.col
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # 语法错误由其它校验负责
    referenced, uninferable = collect_referenced_columns([code])
    if uninferable:
        # ponytail: 动态列名/pl.all() 一律放行 (无法静态证明) — 上限是"漏拦"而非"误杀";
        # 若以后要收紧, 需要一个显式的列白名单声明 (如 META["columns"]) 再复核。
        return []

    problems: list[str] = []
    # polars_expr 的 filter() 表达式直接作用于当日快照, 引擎不会先跑 with_columns →
    # 文件里写了 alias 也不代表这一列存在, 不能按「策略自造列」放行。
    local = set() if backend == "polars_expr" else _local_columns(tree)
    allowed = _allowed_columns(backend, meta)
    registered_signals = _registered_signal_columns()
    missing = sorted(
        name
        for name in referenced
        if name not in allowed
        and not name.startswith(_SIGNAL_PREFIXES)
        and name not in local
        and not (name.startswith(_BUILTIN_SIGNAL_PREFIX) and name in registered_signals)
    )
    if missing:
        detail = "引用了不存在的列: " + ", ".join(missing)
        if backend == "polars_expr":
            detail += " (filter() 只作用于当日快照: 既不会物化自造辅助列, 也没有 MA100 等非内置指标)"
        else:
            detail += " (enriched 只内置 ma5/10/20/30/60; 其它均线需在 filter_history 内自行计算)"
        problems.append(detail)

    # signal_* 单独给提示: 拼错信号名是最常见的「静默买全市场」来源 ——
    # filter() 里 pl.col("signal_typo") 运行期才报缺列, 曾整段回测结果失真。
    bad_signals = sorted(
        name for name in referenced
        if name.startswith(_BUILTIN_SIGNAL_PREFIX) and name not in registered_signals
    )
    if bad_signals:
        available = ", ".join(sorted(registered_signals)[:6]) or "无"
        problems.append(
            "引用了未注册的信号列: " + ", ".join(bad_signals)
            + f"; 可用信号如 {available}…"
            "; 需要新的信号条件请在 filter_history() 里自己算, 不要直接引用不存在的 signal_ 列"
        )

    if backend == "polars_expr":
        ops = sorted(_window_ops(tree))
        if ops:
            problems.append(
                "polars_expr 的 filter() 每日每标的只有一行, 不能用跨日算子 "
                + ", ".join(ops)
                + "; 需要多日判断请改用 python_history_legacy + filter_history()"
            )

    if backend == "python_history_legacy":
        ungrouped = sorted(_ungrouped_window_ops(tree))
        if ungrouped:
            problems.append(
                "filter_history 拿到的是多标的 × 多日面板, 跨日算子 "
                + ", ".join(ungrouped)
                + ' 必须带 .over("symbol") 按标的切窗, 否则会把上一个标的的数据串到下一个标的 '
                "(不报错, 但结果全错); 也可先 group_by(\"symbol\") 再计算"
            )
        required = _required_window(tree, meta)
        if required:
            declared = _declared_lookback(tree, meta)
            if declared is None or declared < required:
                problems.append(
                    f"filter_history 需要至少 {required} 个交易日的历史窗口, "
                    f"但只声明了 LOOKBACK_DAYS={declared or 0}; "
                    f"请声明 LOOKBACK_DAYS = {required} 或更大"
                )
    return problems
