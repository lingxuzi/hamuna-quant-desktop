# 助手技能 (skills)

助手按需读取的「操作手册 / 领域知识」目录。技能是一个目录, 里面一份 `SKILL.md`
(frontmatter + 正文) 与若干参考文件:

```
skills/
└── limit-up/
    ├── SKILL.md
    └── references/
        └── fields.md
```

## 放置位置

| 根目录 | 说明 |
| --- | --- |
| `backend/app/custom/assistant/skills/` | 内置技能, 随仓库分发 |
| `<数据目录>/assistant/skills/` | 用户技能, 同名时覆盖内置 |

数据目录由 `DATA_DIR` 环境变量或 `settings.data_dir` 决定 (默认 `data/`)。

内置技能的**分类归属与启用状态**默认写在 `backend/app/custom/assistant/experts.json`
(随仓库分发); 用户在设置页改过之后写 `<数据目录>/assistant/experts.json` 覆盖。
新增内置技能时要同步补 `experts.json` 里的条目, 否则它会落到「未分类」。

## SKILL.md 格式

```markdown
---
name: limit-up
description: 涨停板梯队与连板高度分析流程
allowed-tools: run_strategy, get_market_overview
---

# 涨停板分析

先看 get_market_overview 的涨停梯队, 再用 run_strategy 跑连板策略。
```

frontmatter 字段:

- `name` 必填, 字母数字与 `-_.`, 需与目录名一致。
- `description` 必填, 一句话说明**什么时候该用这个技能** —— 它是助手判断要不要
  加载技能的唯一依据, 也是 system prompt 里唯一注入的内容。
- `allowed-tools` 可选, 仅作提示 (工具调用仍由助手的确认闸门管)。

## 渐进披露

system prompt 只注入 `name` + `description`; 正文要模型自己调 `load_skill` 取,
更长的补充材料用 `load_skill_reference` 分段取。这样技能数量增加时, 每轮对话
的上下文也不会被撑爆。

## 安全边界

- **只读文本**: 技能目录里的任何脚本都不会被执行 (仓库内没有可信执行沙箱)。
- 参考文件必须落在技能目录内 (路径穿越会被拒绝), 且后缀限 `.md` / `.txt` /
  `.csv` / `.json`。
- 单文件正文上限 6 万字符, 超出截断并标记 `truncated`。
