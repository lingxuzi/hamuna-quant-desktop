"""策略提示词组装器 — 两步定制流程的提示词生成。

职责: 加载对应步骤的 Markdown 指南，拼接用户输入，组装 LLM 提示词。
不知道: LLM 调用、API、前端、引擎执行。
"""
from __future__ import annotations

from pathlib import Path

# 运行时依赖的提示词文档（随 backend/app 打包进 Docker，避免 .dockerignore 排除 docs/ 导致运行时缺失）
_DOCS_DIR = Path(__file__).resolve().parent / "prompts"
_cache: dict[str, str] = {}


def _load_doc(name: str) -> str:
    if name not in _cache:
        path = _DOCS_DIR / name
        _cache[name] = path.read_text(encoding="utf-8") if path.exists() else ""
    return _cache[name]


DIRECTION_CN = {"long": "做多", "short": "做空", "monitor": "监控"}

_BACKEND_AUTO_RULE = """执行后端：由你根据规则自行选择（不要向用户索要）——
- 规则只涉及当日快照（价、量、当日指标、signal_* 信号列）→ polars_expr + filter()
- 规则含「上穿/下穿/金叉/死叉、连续 N 日、N 日新高、涨停后第 X 日、前高/前低、
  最近 N 天内出现过」等任何需要多日比较的条件 → python_history_legacy + filter_history()
- 用户明确点名分钟或矩阵口径时才用 minute_filter / matrix_native"""

_BACKEND_FIXED_RULE = "执行后端：{backend}（必须严格使用，不得自行更换）"


def _backend_rule(execution_backend: str) -> str:
    if execution_backend == "auto":
        return _BACKEND_AUTO_RULE
    return _BACKEND_FIXED_RULE.format(backend=execution_backend)


def _format_basic_filter(basic_filter: dict) -> str:
    """dict → Python 字面量单行 (True/False/None 保持 Python 语法, 供 AI 直接抄进 META)。"""
    return repr(dict(basic_filter))


def build_step1(
    name: str,
    description: str,
    direction: str,
    rules: str,
    strategy_id: str = "",
    execution_backend: str = "polars_expr",
    basic_filter: dict | None = None,
) -> str:
    """步骤1：规则 → 完整策略代码（参数 + 信号 + 评分）

    注意: 生成规范已在 ai_generator.py 的 system prompt 中加载，
    此处只拼用户输入以降低网关超时概率。
    basic_filter: 用户「默认基础参数」(策略页设置); None = 不约束, AI 自行默认。
    """
    id_line = f"\n策略ID（必须使用此ID）：{strategy_id}" if strategy_id else ""
    bf_line = (
        f"\n用户默认基础过滤参数（META 的 basic_filter 必须严格使用以下值，"
        f"除非策略规则与之明确冲突并在代码注释中说明）：\n{_format_basic_filter(basic_filter)}\n"
        if basic_filter
        else ""
    )

    return f"""请根据以下用户输入生成完整策略代码：

策略名称：{name}{id_line}
策略描述：{description}
策略方向：{DIRECTION_CN.get(direction, direction)}
{_backend_rule(execution_backend)}
{bf_line}策略规则：
{rules}

输出要求：
1. 严格遵循系统提示中的策略文件结构和安全限制。
2. 执行后端与入口函数必须一致：matrix_native 只定义 MATRIX_STRATEGY，polars_expr 只定义 filter()，python_history_legacy 只定义 filter_history() 并声明 LOOKBACK_DAYS 与 REQUIRED_FEATURES，minute_filter 只定义 filter_minute_history(df, params) 且 timeframes 必须为 ["1m"]。
3. META 必须声明 asset_types 和 timeframes，并生成完整执行与交易元数据。
4. 只输出 Python 代码。"""


def build_step2(current_code: str, instruction: str) -> str:
    """步骤2：修改策略任意部分"""
    guide = _load_doc("strategy-builder-step2.md")

    return f"""{guide}

---

当前策略代码：
```python
{current_code}
```

用户修改指令：
{instruction}

只输出修改后的完整 Python 代码。"""
