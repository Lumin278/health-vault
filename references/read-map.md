# 读文件地图（什么时候读哪份）

`SKILL.md` 是纲领（干什么、硬边界、流程）。**本文件只管一件事**：哪一步该读哪份、该跑哪个脚本。被指到的文件正文不在这里重复。

## 一、加载节奏

| 层 | 什么时候进来 |
|---|---|
| `SKILL.md` | 任务像入库动作时（按 description 触发），整份读 —— 闲聊不读 |
| 库根 `AGENTS.md`（铭牌） | cwd 在库内 → **会话启动时自动注入**；只有库身份与结构，**不含规矩** |
| `references/` `knowledge/` `templates/` `assets/` `examples/` | **按需**，见下面矩阵 |

## 二、任务 → 读哪份

| 要做的事 | 读 |
|---|---|
| 定位库 / 判断库内还是库外 | `SKILL.md` §库位置与结构（找库五步）· §边界：只在库内工作 |
| 建库 · 校验库结构 | `references/vault-spec.md` ＋ `assets/skeleton/` ＋ `scripts/init_vault.py` |
| 读图抽字段（姓名 / 机构 / 日期 / 数值 / 结论） | `references/entity-schema.md` |
| 用户说的是口语（肚子疼 / 拉肚子） | `references/terminology_map.md` |
| 写某个目录的内容之前 | `references/vault-dirs/〈目录名〉.md` —— `2-检查报告` `3-不适记录` `4-就医记录` `6-用药` `7-视图` `8-体重管理` `9-药品管理` `10-血糖数据` `attachments` |
| 写 `1-基本信息` / `5-指标` | **没有单独的目录说明** —— 口径在 `SKILL.md` §核心模型 · §指标总表 |
| 生成页面（汇总页 / 单报告页 / 三视图） | `templates/`（七份骨架） |
| 出给医生的报告 / 给家人讲人话 | `SKILL.md` §面向医生的 PDF ＋ `references/plain-language-rules.md` |
| 查药 · 查两药相互作用 | `knowledge/用药/规范.md`（**先看这份**）→ `索引.md` · `来源与许可.md` |
| 改库结构 · 改本技能的定案之前 | `references/design-notes.md`（为什么这么定） |
| 拿不准格式 | `examples/归档一次体检.md` |

## 三、跑哪个脚本、什么时候

| 脚本 | 什么时候跑 |
|---|---|
| `scripts/init_vault.py` | 只有建库时（一辈子一次）；`--plaque-only` 只重写铭牌 |
| `scripts/drugdb/q.py` | 用户问药时；查两药相互作用用 `-i 药A 药B` |
| `scripts/drugdb/build.py` | 说明书库不存在、或要换数据源时（**数据不随技能分发**） |

「什么时候问用户」不在这里重复列 —— 见 `SKILL.md` §准入校验 与 §六条交互规矩。

## 四、Hermes 怎么找这些文件（机制事实，改结构前先知道）

- **会被自动注入的文件名**（优先级从高到低，同一目录第一个命中为准）：
  `AGENTS.override.md` → `AGENTS.md` → `agents.md` → `CLAUDE.md` → `claude.md` → `.cursorrules`。
  **其它名字一律不注入** —— 写 `NOTES.md`、`README.md` 之类指望它被自动读到，是无效的。
- **两个时机**：① 会话启动 → cwd（库根）那份进系统上下文；② 干活中每次调用带路径的工具（读 / 写 / 终端 / `cd`）时，取那个目录**及其向上 5 层**当候选，命中 `AGENTS.md` 就追加进那次工具结果（不碰系统提示，prompt 缓存得以保留）。
- **防线与上限**：符号链接指向工作目录之外、或指向 `.env` 这类敏感文件 → 拒绝加载；单份 32 KiB 上限，官方建议控制在 ~8k 以内（本库铭牌 1.7k）。
- **可以关掉**：`--ignore-rules` 一次性关闭 AGENTS.md / SOUL.md / .cursorrules / 记忆 / 预加载技能的注入。
- **项目里存不了指导意见**：`~/.hermes/projects.db` 的字段只有 id / slug / name / description / icon / color / board_slug / primary_path / created_at / archived。项目的作用是**把会话的 cwd 钉在库根** —— 所以「项目下的 AGENTS.md」与「库根那份」是**同一个文件**，不是两份。
- **本技能怎么被触发**：技能是 profile 级（全局，所有会话都在列），Hermes 只看 `SKILL.md` 的 description；任务像入库动作才读全文。库外会话也走它，但**写之前先问**。
- **并发会话**：开工前先 `git status`（工作区应干净）；工具报 sibling 改动 → 先读最新内容 ＋ `git diff HEAD` 比对，再用定点 `patch`，**绝不整篇重写**别人刚改过的文件。
