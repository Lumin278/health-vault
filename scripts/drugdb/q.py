#!/usr/bin/env python3
"""药品库查询（本机个人使用）。输出刻意保持精简，避免把大段说明书塞进上下文。

用法：
  python3 q.py 布洛芬                 # 查药：命中概况（剂型清单 + 关键字段摘要）
  python3 q.py --full 布洛芬          # 同上，但不截断（按需才用）
  python3 q.py --field 禁忌 布洛芬     # 只看某个字段：适应症|禁忌|不良反应|相互作用|成分|用法|警告|储存|批准文号
  python3 q.py -i 阿司匹林 布洛芬      # 查两药相互作用
  python3 q.py -s 头痛                # 全文搜索（名称/成分/适应症/不良反应），只回药名
  python3 q.py -n 布洛芬              # 只要规范名 + 成分 + 剂型清单
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(
    os.environ.get("DRUGDB") or (Path.home() / "drugdb" / "drugs.sqlite")
).expanduser()
FORMS = (
    "注射液",
    "注射用",
    "葡萄糖注射液",
    "氯化钠注射液",
    "片",
    "胶囊",
    "缓释片",
    "缓释胶囊",
    "分散片",
    "肠溶片",
    "肠溶胶囊",
    "颗粒",
    "口服液",
    "口服溶液",
    "溶液",
    "混悬液",
    "滴剂",
    "乳膏",
    "软膏",
    "凝胶",
    "栓",
    "贴",
    "喷雾剂",
    "气雾剂",
    "滴眼液",
    "滴鼻液",
    "滴耳液",
    "糖浆",
    "散",
    "丸",
    "合剂",
    "酊",
    "膏",
    "洗剂",
    "搽剂",
    "吸入剂",
    "粉雾剂",
)
FIELD_MAP = {
    "适应症": "indication",
    "适应": "indication",
    "用于": "indication",
    "禁忌": "contraindications",
    "不良反应": "adverse_reactions",
    "副作用": "adverse_reactions",
    "相互作用": "drug_interactions",
    "成分": "composition",
    "用法": "dosage",
    "剂量": "dosage",
    "警告": "warnings",
    "储存": "storage",
    "批准文号": "approval_number",
    "批准": "approval_number",
}
CANONICAL_FIELDS = set(FIELD_MAP.values())


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def bounded_limit(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是 1-100 的整数") from exc
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError("必须是 1-100 的整数")
    return value


def connect() -> sqlite3.Connection:
    if not DB.expanduser().is_file():
        sys.exit(f"药品库不存在：{DB.expanduser()}（先跑 build.py）")
    try:
        uri = DB.expanduser().resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error) as exc:
        sys.exit(f"打不开药品库：{DB.expanduser()}（{exc}）")

    con.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        missing = {"drugs", "interactions", "drugs_fts"} - tables
        if missing:
            raise sqlite3.DatabaseError(f"缺少表：{', '.join(sorted(missing))}")
        con.execute("PRAGMA query_only = ON")
    except sqlite3.DatabaseError as exc:
        con.close()
        sys.exit(f"药品库结构不完整：{DB.expanduser()}（{exc}）")
    return con


def generic(name: str) -> str:
    """去掉剂型后缀，得到通用名近似（用于把 布洛芬缓释胶囊 → 布洛芬）。"""
    g = name
    for form in sorted(FORMS, key=len, reverse=True):
        if g.endswith(form) and len(g) > len(form):
            g = g[: -len(form)]
            break
    return g.strip("（）() ")


def like_escape(value: str) -> str:
    """让用户输入中的 % / _ / \\ 按普通字符匹配。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(value: str) -> str:
    return f"%{like_escape(value)}%"


def find(con: sqlite3.Connection, kw: str, limit: int = 10):
    """精确 → 前缀 → 成分包含 → 名称包含，逐级放宽。"""
    rows = con.execute(
        "SELECT * FROM drugs WHERE name = ? ORDER BY LENGTH(name) ASC", (kw,)
    ).fetchall()
    if not rows:
        prefix = like_escape(kw) + "%"
        rows = con.execute(
            "SELECT * FROM drugs WHERE name LIKE ? ESCAPE '\\' "
            "ORDER BY LENGTH(name) ASC LIMIT ?",
            (prefix, limit),
        ).fetchall()
    if not rows:
        pattern = like_contains(kw)
        rows = con.execute(
            "SELECT * FROM drugs WHERE composition LIKE ? ESCAPE '\\' "
            "ORDER BY LENGTH(name) ASC LIMIT ?",
            (pattern, limit),
        ).fetchall()
    if not rows:
        pattern = like_contains(kw)
        rows = con.execute(
            "SELECT * FROM drugs WHERE name LIKE ? ESCAPE '\\' "
            "ORDER BY LENGTH(name) ASC LIMIT ?",
            (pattern, limit),
        ).fetchall()
    return rows


def trunc(value: str | None, n: int) -> str:
    text = (value or "").strip()
    return (
        text
        if len(text) <= n
        else text[:n] + f" …（共 {len(text)} 字，用 --full 看全文）"
    )


def resolve_field(value: str) -> str | None:
    key = FIELD_MAP.get(value.strip(), value.strip())
    return key if key in CANONICAL_FIELDS else None


def cmd_lookup(con: sqlite3.Connection, args) -> int:
    rows = find(con, args.kw, args.limit)
    if not rows:
        print(
            f"库里没有匹配「{args.kw}」的条目。按规矩：请向用户确认药名（药盒上的字最准），或换关键词。"
        )
        return 0

    field = resolve_field(args.field) if args.field else None
    if args.field and not field:
        allowed = "适应症|禁忌|不良反应|相互作用|成分|用法|警告|储存|批准文号"
        print(f"未知字段「{args.field}」；可用：{allowed}", file=sys.stderr)
        return 2

    head = rows[0]
    columns = set(head.keys())
    if field and field not in columns:
        print(
            f"当前药品库没有「{args.field}」字段，请用新版 build.py 重建数据库",
            file=sys.stderr,
        )
        return 2
    print(f"## 命中 {len(rows)} 条（通用名近似：{generic(head['name'])}）")
    print("剂型/条目：" + "、".join(row["name"] for row in rows[:12]))
    if args.name_only:
        print(
            f"成分：{head['composition'].strip() if head['composition'] else '[信息缺失]'}"
        )
        return 0

    fields = [
        ("成分", "composition"),
        ("适应症", "indication"),
        ("禁忌", "contraindications"),
        ("不良反应", "adverse_reactions"),
        ("相互作用", "drug_interactions"),
    ]
    if field:
        fields = [(args.field.strip(), field)]
    print(f"\n（以最短条目「{head['name']}」为准）")
    for label, column in fields:
        value = head[column]
        if value:
            shown = value if args.full else trunc(value, 420)
            print(f"\n【{label}】{shown}")
    return 0


def cmd_interaction(con: sqlite3.Connection, args) -> int:
    a, b = args.a, args.b
    a_pattern, b_pattern = like_contains(a), like_contains(b)
    rows = con.execute(
        "SELECT * FROM interactions "
        "WHERE (a LIKE ? ESCAPE '\\' AND b LIKE ? ESCAPE '\\') "
        "OR (b LIKE ? ESCAPE '\\' AND a LIKE ? ESCAPE '\\') LIMIT ?",
        (a_pattern, b_pattern, a_pattern, b_pattern, args.limit),
    ).fetchall()
    if rows:
        print(f"## 「{a}」×「{b}」共 {len(rows)} 条")
        for row in rows:
            risk = f"[{row['risk']}] " if row["risk"] else ""
            print(f"- {risk}{row['a']} + {row['b']}：{trunc(row['detail'], 300)}")
        return 0

    print(
        f"## 「{a}」×「{b}」没有直接配对记录。下面分别列出两药各自的相互作用提示（供参考，非两药直配）："
    )
    for kw in (a, b):
        pattern = like_contains(kw)
        sub = con.execute(
            "SELECT * FROM interactions "
            "WHERE a LIKE ? ESCAPE '\\' OR b LIKE ? ESCAPE '\\' LIMIT ?",
            (pattern, pattern, args.limit),
        ).fetchall()
        print(f"\n【{kw}】相关 {len(sub)} 条：")
        if not sub:
            print("- （库中无记录）")
        for row in sub:
            risk = f"[{row['risk']}] " if row["risk"] else ""
            print(f"- {risk}{row['a']} + {row['b']}：{trunc(row['detail'], 220)}")
    print(
        "\n> 提醒：以上只是说明书原文摘录，**不做任何判断**；用药问题请咨询医生或药师。"
    )
    return 0


def search_terms(value: str) -> list[str]:
    return re.findall(r"[\w\u3400-\u9fff]+", value, flags=re.UNICODE)


def fts_query(value: str) -> str | None:
    """生成安全的 FTS5 查询；trigram 不足 3 字的词交给 LIKE 回退。"""
    terms = search_terms(value)
    if not terms or any(len(term) < 3 for term in terms):
        return None
    return " AND ".join(f'"{term}"' for term in terms)


def search_like(con: sqlite3.Connection, value: str, limit: int):
    """短中文词或旧版 FTS 索引的回退搜索。"""
    if not search_terms(value):
        return []
    pattern = like_contains(value)
    return con.execute(
        "SELECT name, composition FROM drugs WHERE "
        "name LIKE ? ESCAPE '\\' OR composition LIKE ? ESCAPE '\\' "
        "OR indication LIKE ? ESCAPE '\\' OR adverse_reactions LIKE ? ESCAPE '\\' "
        "ORDER BY LENGTH(name) LIMIT ?",
        (pattern, pattern, pattern, pattern, limit),
    ).fetchall()


def cmd_search(con: sqlite3.Connection, args) -> int:
    query = fts_query(args.kw)
    rows = []
    if query:
        try:
            rows = con.execute(
                "SELECT d.name AS name, d.composition AS composition "
                "FROM drugs_fts f JOIN drugs d ON d.rowid = f.rowid "
                "WHERE drugs_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, args.limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    if not rows:
        rows = search_like(con, args.kw, args.limit)
    if not rows:
        print(f"全文搜索无结果：{args.kw}")
        return 0
    print(f"## 全文搜索「{args.kw}」→ {len(rows)} 条（只回名称，需要细节再查具体药）")
    for row in rows:
        suffix = f"（成分：{row['composition']}）" if row["composition"] else ""
        print(f"- {row['name']}{suffix}")
    return 0


def main() -> int:
    global DB
    configure_stdio()
    ap = argparse.ArgumentParser(description="本地药品库查询")
    ap.add_argument("kw", nargs="?", help="药名（任何写法）")
    ap.add_argument("--full", action="store_true", help="不截断字段")
    ap.add_argument(
        "--field",
        help="只看某字段：适应症|禁忌|不良反应|相互作用|成分|用法|警告|储存|批准文号",
    )
    ap.add_argument(
        "--db",
        help=f"说明书库路径（默认 $DRUGDB 或 {Path.home() / 'drugdb' / 'drugs.sqlite'}）",
    )
    ap.add_argument(
        "--limit", type=bounded_limit, default=10, help="最多返回 1-100 条（默认 10）"
    )
    ap.add_argument("-i", "--interaction", nargs=2, metavar=("药A", "药B"))
    ap.add_argument("-s", "--search", metavar="关键词")
    ap.add_argument("-n", "--name-only", action="store_true")
    args = ap.parse_args()
    if args.db:
        DB = Path(args.db).expanduser()
    if args.interaction and args.search:
        ap.error("--interaction 与 --search 不能同时使用")
    if not args.kw and not args.interaction and not args.search:
        ap.print_help()
        return 1

    con = connect()
    try:
        if args.interaction:
            args.a, args.b = args.interaction
            return cmd_interaction(con, args)
        if args.search:
            args.kw = args.search
            return cmd_search(con, args)
        return cmd_lookup(con, args)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
