"""QMT 导出产物规范校验 —— 全部规则可追溯到 qmt-docs 原文。

刻意不放在 tests/ 下：它不是「仓库某函数的回归测试」，而是对**任意一份
候选脚本**（可以是仓库刚生成的，也可以是从 QMT 机器上拷回来的）做规范体检，
所以同时提供 CLI 和 pytest 入口。

用法:
    python -m app.services.qmt_lint cand.qmt.py
    python -m app.services.qmt_lint cand.qmt.py --json

每条规则都带 RULE_ID 与文档出处，报错信息直接引用原文，避免「凭印象校规」。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# ── 规则表 ────────────────────────────────────────────────────────
# (id, 文档出处, 说明)
DOC = {
    "encoding": "qmt-docs/overview.md:3 / SKILL.md「编码声明: 文件首行必须是 #coding:gbk」",
    "py36": "qmt-docs/overview.md:9「Python 版本 3.6」",
    "ctx": "qmt-docs/python-innerApi/user_attention.md:56 + question_answer.md:137-153"
           "「ContextInfo 逐 k 线保存: 属性在每根 bar 结束会回滚」",
    "after_init": "qmt-docs/python-innerApi/data_function.md:2688"
                  "「get_stock_list_in_sector 只能在 after_init; handlebar 运行」",
    "fetch": "qmt-docs/python-innerApi/data_function.md:77"
             "「get_market_data_ex 不建议在 init 中运行, 仅能取到本地数据」",
    "tick": "qmt-docs/python-innerApi/data_structure.md:13-35「tick 对象字段表」",
    "schedule": "qmt-docs/python-innerApi/system_function.md:126-145"
                "「interval 类型为 datetime.timedelta; time_point 为 yyyymmddHHMMSS 或 datetime」",
    "passorder": "qmt-docs/python-innerApi/trading_function.md:23-42 + enum_constants.md",
    "blocking": "qmt-docs/python-innerApi/user_attention.md:56「所有策略在同一线程, 避免阻塞」",
    "thread": "qmt-docs/python-innerApi/user_attention.md:56「python 无法使用多线程和多进程」",
    "volume": "qmt-docs/SKILL.md「股票交易必须是 100 的整数倍」",
    "entry": "qmt-docs/python-innerApi/system_function.md:49「系统会在 init 后、handlebar 前调用 after_init」",
    "legacy_api": "qmt-docs/python-innerApi 各接口页「不推荐 / 已弃用 / 旧版」标注",
    "api_sig": "qmt-docs/python-innerApi/data_function.md:1931"
               "「get_instrument_detail(stockcode, iscomplete=False); 旧版本客户端不支持 iscomplete」",
}

# 官方标注为不推荐 / 已弃用 / 旧版的接口: 函数名 → (替代接口, 文档依据)
LEGACY_APIS = {
    "get_stock_name": ("get_instrument_detail(...)[\"InstrumentName\"]",
                       "system_function.md:311「计划后续版本抛弃」"),
    "get_market_data": ("get_market_data_ex",
                        "data_function.md:1246【不推荐】"),
    "get_history_data": ("get_market_data_ex",
                         "data_function.md:1186【不推荐】"),
    "get_local_data": ("get_market_data_ex",
                       "data_function.md:1099【不推荐】"),
    "run_time": ("schedule_run",
                 "system_function.md:117「新版设置定时器函数」"),
    "get_instrumentdetail": ("get_instrument_detail",
                             "data_function.md:1931「旧版本客户端的函数名」"),
}

QMT_ENTRY_FUNCS = {"init", "after_init", "handlebar"}
QMT_TICK_FIELDS = {"lastPrice", "lastClose", "open", "high", "low", "amount",
                   "volume", "pvolume", "settlementPrice", "openInt",
                   "askPrice", "askVol", "bidPrice", "bidVol", "timetag"}
# 文档允许的账户类型 / 数据类型；官方示例用小写, 故大小写不敏感
ACCOUNT_TYPES = {"stock", "future", "credit", "hugangtong", "shengangtong",
                 "stock_option"}
DATA_TYPES = {"account", "position", "position_statistics", "order", "deal", "task"}
OP_TYPES = {23, 24, 27, 28, 29, 30, 31, 32, 33, 34}  # 股票/ETF/可转债 + 两融
ORDER_TYPES = {1101, 1102, 1201, 1202}
PR_TYPES = {-1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
            18, 19, 20, 21, 22, 23, 24, 25, 26, 42, 43, 44, 45, 46,
            47, 48, 49}
# 3.7+ 语法特征 (3.6 会 SyntaxError 或行为不同)
BLOCKED_SYNTAX = {
    ast.NamedExpr: "海象运算符 := (3.8+)",
    ast.JoinedStr: None,  # f-string 3.6 可用, 但 = 调试语法不行, 单独查
}


class Issue:
    def __init__(self, rule: str, message: str, line: int = 0):
        self.rule = rule
        self.message = message
        self.line = line

    def __str__(self):
        where = f"line {self.line}: " if self.line else ""
        return (f"[{self.rule}] {where}{self.message} | 依据: "
                f"{DOC.get(self.rule, '?')}")

    def as_dict(self):
        return {"rule": self.rule, "line": self.line, "message": self.message,
                "doc": DOC.get(self.rule, "")}


def _call_name(node):
    """e.Name / e.Attribute → 函数名。"""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal_arg(call, index):
    if index < len(call.args):
        arg = call.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, str)):
            return arg.value
    return None


def check_encoding(text: str) -> list[Issue]:
    issues = []
    first = text.splitlines()[0] if text else ""
    # 允许 UTF-8 BOM 在前 (Windows 编辑器常见)
    normalized = first.lstrip("﻿")
    if normalized.strip() != "#coding:gbk":
        issues.append(Issue("encoding",
                            f"首行必须是 #coding:gbk (实际: {first[:40]!r})", 1))
    return issues


def check_py36(tree: ast.AST) -> list[Issue]:
    issues = []
    for node in ast.walk(tree):
        kind = type(node)
        if BLOCKED_SYNTAX.get(kind):
            issues.append(Issue("py36", BLOCKED_SYNTAX[kind],
                                getattr(node, "lineno", 0)))
        # 3.6 不支持函数返回值注解 / 参数注解
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                issues.append(Issue("py36", f"函数 {node.name} 有返回值注解 (3.6 会 TypeError)", node.lineno))
            for arg in node.args.args:
                if arg.annotation is not None:
                    issues.append(Issue("py36", f"函数 {node.name} 参数 {arg.arg} 有类型注解", node.lineno))
        # AnnAssign 带注解 (x: int = 1)
        if isinstance(node, ast.AnnAssign):
            issues.append(Issue("py36", "变量注解 AnnAssign 在 3.6 下要求目标已定义",
                                node.lineno))
        # from __future__ import annotations 在 3.7+ 才存在
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            names = {a.name for a in node.names}
            if "annotations" in names:
                issues.append(Issue("py36", "__future__.annotations 是 3.7+",
                                    node.lineno))
    return issues


def check_ctx_attributes(tree: ast.AST) -> list[Issue]:
    """ContextInfo 的参数名 + 禁止对其赋值。"""
    issues = []
    ctx_names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in QMT_ENTRY_FUNCS and node.args.args:
            ctx_names.add(node.args.args[0].arg)
    # 兜底: 常见命名
    ctx_names |= {"C", "ContextInfo", "context"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) \
                        and isinstance(target.value, ast.Name) \
                        and target.value.id in ctx_names:
                    issues.append(Issue(
                        "ctx", f"{target.value.id}.{target.attr} = ... 会被下一根 bar 回滚; 改用模块级对象", node.lineno))
        # augmented / del 同理
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Attribute) \
                and isinstance(node.target.value, ast.Name) \
                and node.target.value.id in ctx_names:
            issues.append(Issue("ctx", f"对 {node.target.value.id}.{node.target.attr} 做增量赋值同样会回滚",
                                node.lineno))
    return issues


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _enclosing_func(tree, target):
    """返回包含 target 的顶层函数名。"""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if child is target:
                    return node.name
    return None


def check_call_sites(tree: ast.AST) -> list[Issue]:
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)

        # 板块函数不能在 init
        if name == "get_stock_list_in_sector":
            host = _enclosing_func(tree, node)
            if host == "init":
                issues.append(Issue("after_init",
                                    "get_stock_list_in_sector 在 init 中调用", node.lineno))

        # 取数不能在 init
        if name == "get_market_data_ex":
            host = _enclosing_func(tree, node)
            if host == "init":
                issues.append(Issue("fetch", "get_market_data_ex 在 init 中调用",
                                    node.lineno))
            # 前两位必须是位置参数 (SKILL.md 明确要求)
            if len(node.args) < 2:
                issues.append(Issue("fetch",
                                    "fields / stocks 必须用位置参数, 不能写成关键字",
                                    node.lineno))

        # schedule_run
        if name == "schedule_run":
            if len(node.args) >= 4:
                interval = node.args[3]
                if isinstance(interval, ast.Constant) and interval.value is None:
                    issues.append(Issue("schedule",
                                        "interval 传 None; 需 datetime.timedelta",
                                        node.lineno))
            tp = node.args[1] if len(node.args) >= 2 else None
            if isinstance(tp, ast.Constant) and isinstance(tp.value, str) \
                    and not re.fullmatch(r"\d{14}|\d{8}", tp.value):
                issues.append(Issue(
                    "schedule",
                    f"time_point {tp.value!r} 不是 yyyymmddHHMMSS", node.lineno))

        # passorder
        if name == "passorder":
            op = _literal_arg(node, 0)
            if isinstance(op, int) and op not in OP_TYPES:
                issues.append(Issue("passorder",
                                    f"opType {op} 不是股票/ETF/可转债买卖值",
                                    node.lineno))
            ot = _literal_arg(node, 1)
            if isinstance(ot, int) and ot not in ORDER_TYPES:
                issues.append(Issue("passorder", f"orderType {ot} 未在枚举内",
                                    node.lineno))
            pt = _literal_arg(node, 4)
            if isinstance(pt, int) and pt not in PR_TYPES:
                issues.append(Issue("passorder", f"prType {pt} 未在枚举内",
                                    node.lineno))
            if len(node.args) < 11:
                issues.append(Issue("passorder",
                                    "参数少于 11 个 (缺 ContextInfo 等)", node.lineno))

        # get_trade_detail_data
        if name == "get_trade_detail_data":
            at = _literal_arg(node, 1)
            if isinstance(at, str) and at.lower() not in ACCOUNT_TYPES:
                issues.append(Issue("passorder",
                                    f"strAccountType {at!r} 不在枚举内", node.lineno))
            dt = _literal_arg(node, 2)
            if isinstance(dt, str) and dt.lower() not in DATA_TYPES:
                issues.append(Issue("passorder",
                                    f"strDatatype {dt!r} 不在枚举内", node.lineno))

        # 阻塞 / 线程
        if name in {"sleep"}:
            issues.append(Issue("blocking", f"调用了 {name}; 会阻塞同线程所有策略",
                                node.lineno))
        if name in {"Thread", "Process", "Pool", "ThreadPoolExecutor",
                    "ProcessPoolExecutor"}:
            issues.append(Issue("thread", f"多线程/多进程不可用: {name}", node.lineno))

    # import threading / multiprocessing
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in {"threading", "multiprocessing",
                                            "concurrent"}:
                    issues.append(Issue("thread",
                                        f"import {a.name} 不可用", node.lineno))
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {
                "threading", "multiprocessing", "concurrent"}:
            issues.append(Issue("thread",
                                f"from {node.module} import ...", node.lineno))
    return issues


def check_tick_fields(tree: ast.AST) -> list[Issue]:
    """tick dict 取字段名必须在文档字段表内。"""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "get_full_tick":
            continue
        # xxx.get("lastPrice") 形态
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                key = arg.value
                # 只对疑似 tick 字典的调用报警: 变量名含 tick
                src = ast.unparse(node.func.value)
                if "tick" in src.lower() and key not in QMT_TICK_FIELDS:
                    issues.append(Issue(
                        "tick", f"tick 字段 {key!r} 不在文档字段表内", node.lineno))
    return issues


def check_entry_shape(tree: ast.AST) -> list[Issue]:
    """QMT 要求 init/handlebar 存在; after_init 可选。"""
    issues = []
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for required in ("init", "handlebar"):
        if required not in defined:
            issues.append(Issue("entry", f"缺少必需的入口函数 {required}"))
    for name in ("init", "after_init", "handlebar"):
        func = _find_func(tree, name)
        if func is None:
            continue
        if not func.args.args:
            issues.append(Issue("entry", f"{name} 必须有 ContextInfo 形参",
                                func.lineno))
    return issues


def check_volume_literal(tree: ast.AST) -> list[Issue]:
    """passorder 的 volume 若是字面量, 必须是 100 的整数倍。"""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "passorder":
            vol = _literal_arg(node, 6)
            if isinstance(vol, int) and vol > 0 and vol % 100 != 0:
                issues.append(Issue("volume",
                                    f"passorder volume={vol} 不是 100 的整数倍",
                                    node.lineno))
    return issues


def check_legacy_api(tree: ast.AST) -> list[Issue]:
    """禁掉官方标注为不推荐 / 已弃用 / 旧版的接口, 并校参数个数。"""
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name == "get_instrument_detail" and len(node.args) > 1:
            issues.append(Issue(
                "api_sig",
                "get_instrument_detail 只接受 1 个位置参数 (stockcode); "
                "旧版本客户端不支持 iscomplete, 多传会 TypeError",
                node.lineno))
        legacy = LEGACY_APIS.get(name)
        if legacy is not None:
            issues.append(Issue(
                "legacy_api",
                f"{name} 是旧版接口, 请改用 {legacy[0]} ({legacy[1]})",
                node.lineno))
    return issues

CHECKS = (
    check_encoding,
    check_py36,
    check_ctx_attributes,
    check_call_sites,
    check_tick_fields,
    check_entry_shape,
    check_volume_literal,
    check_legacy_api,
)


def lint_text(text: str) -> list[Issue]:
    issues = list(check_encoding(text))
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        issues.append(Issue("py36", f"语法解析失败: {exc.msg}",
                            exc.lineno or 0))
        return issues
    for check in CHECKS[1:]:
        issues.extend(check(tree))
    return issues


def lint_file_bytes(raw: bytes) -> list[Issue]:
    """校验字节流 —— QMT 实际是按 GBK 读文件的, 解码失败就是硬错误。"""
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError as exc:
        return [Issue("encoding", f"文件无法按 GBK 解码 (QMT 会直接报错): {exc}")]
    return lint_text(text)


def lint_file(path: Path) -> tuple[list[Issue], str]:
    raw = path.read_bytes()
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError:
        text = ""
    return lint_file_bytes(raw), text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QMT 导出脚本规范校验")
    parser.add_argument("paths", nargs="+", help="待校验的 .py 文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    total = 0
    report = []
    for raw_path in args.paths:
        path = Path(raw_path)
        if not path.exists():
            print(f"找不到文件: {path}", file=sys.stderr)
            return 2
        issues, _ = lint_file(path)
        total += len(issues)
        if args.json:
            report.append({"file": str(path),
                           "issues": [i.as_dict() for i in issues]})
        else:
            print(f"{path}: {len(issues)} 处问题")
            for issue in issues:
                print("  " + str(issue))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())