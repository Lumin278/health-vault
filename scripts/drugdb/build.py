#!/usr/bin/env python3
"""构建本地药品库（SQLite + FTS5）。

用法：
    python3 build.py --src drug_db_v2.json --out ~/drugdb/drugs.sqlite
产出：drugs.sqlite（drugs 表 + interactions 表 + drugs_fts 全文索引）
数据与许可：见技能 knowledge/用药/来源与许可.md —— 不随技能分发任何药品数据。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path


def env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


SRC = env_path("DRUGDB_SRC", Path("drug_db_v2.json"))
OUT = env_path("DRUGDB", Path.home() / "drugdb" / "drugs.sqlite")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = "；".join(str(x) for x in v if x)
    return re.sub(r"\s+", " ", str(v)).strip()


# 兼容其他来源的相互作用键名（本机现用来源是 "pair"/"risk"/"note"）。
INTER_KEYS = (
    ("drug1", "drug2", "interaction"),
    ("a", "b", "detail"),
    ("name1", "name2", "description"),
)


def pick_interaction(it: dict) -> tuple[str, str, str, str] | None:
    """返回 (药A, 药B, 风险, 说明)；结构不认识时返回 None，不猜字段。"""
    pair = it.get("pair")
    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
        a, b = norm(pair[0]), norm(pair[1])
        detail = norm(it.get("note") or it.get("detail") or it.get("description"))
        return (a, b, norm(it.get("risk")), detail) if a and b else None

    for k1, k2, k3 in INTER_KEYS:
        if it.get(k1) and it.get(k2):
            a, b = norm(it.get(k1)), norm(it.get(k2))
            detail = norm(it.get(k3)) or json.dumps(it, ensure_ascii=False)
            return (a, b, norm(it.get("risk")), detail) if a and b else None
    return None


def load_payload(path: Path) -> tuple[dict, list]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"读不到原始数据：{exc}") from exc
    except UnicodeError as exc:
        raise ValueError("原始数据不是有效的 UTF-8 文本") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise TypeError("JSON 顶层必须是对象")
    drugs = data.get("drugs")
    if drugs is None:
        drugs = {}
    if not isinstance(drugs, dict):
        raise TypeError("drugs 必须是对象：{药品名: 字段对象}")

    interactions = data.get("interactions")
    if interactions is None:
        interactions = []
    if isinstance(interactions, dict):
        interactions = list(interactions.values())
    if not isinstance(interactions, list):
        raise TypeError("interactions 必须是数组")
    return drugs, interactions


def build_database(
    out: Path, drugs: dict, interactions: list
) -> tuple[int, int, int, int, int]:
    """先写同目录临时库，成功后再原子替换；失败不破坏已有库。"""
    if out.exists() and not out.is_file():
        raise ValueError(f"输出路径不是文件：{out}")
    out.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{out.name}.", suffix=".tmp", dir=out.parent
    )
    os.close(fd)
    tmp = Path(tmp_name)
    con: sqlite3.Connection | None = None

    try:
        con = sqlite3.connect(tmp)
        fts_tokenizer = ""
        try:
            con.execute(
                "CREATE VIRTUAL TABLE temp.fts_probe USING fts5(x, tokenize='trigram')"
            )
            con.execute("DROP TABLE temp.fts_probe")
            fts_tokenizer = ", tokenize='trigram'"
        except sqlite3.OperationalError:
            pass
        con.executescript(
            """
            CREATE TABLE drugs(
                name TEXT PRIMARY KEY, composition TEXT, indication TEXT,
                contraindications TEXT, warnings TEXT, dosage TEXT,
                adverse_reactions TEXT, drug_interactions TEXT, storage TEXT,
                approval_number TEXT, source TEXT
            );
            CREATE TABLE interactions(a TEXT, b TEXT, risk TEXT, detail TEXT);
            """
        )

        rows = []
        seen = set()
        skipped_drugs = duplicate_drugs = 0
        for raw_name, value in drugs.items():
            if not isinstance(value, dict):
                skipped_drugs += 1
                continue
            name = norm(raw_name)
            if not name:
                skipped_drugs += 1
                continue
            if name in seen:
                duplicate_drugs += 1
            seen.add(name)
            rows.append(
                (
                    name,
                    norm(value.get("composition")),
                    norm(value.get("indication")),
                    norm(value.get("contraindications")),
                    norm(value.get("warnings")),
                    norm(value.get("dosage")),
                    norm(value.get("adverse_reactions")),
                    norm(value.get("drug_interactions")),
                    norm(value.get("storage")),
                    norm(value.get("approval_number") or value.get("approval_no")),
                    norm(value.get("source")),
                )
            )
        con.executemany(
            "INSERT OR REPLACE INTO drugs VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
        )

        irows = []
        skipped_interactions = 0
        for item in interactions:
            row = pick_interaction(item) if isinstance(item, dict) else None
            if row is None:
                skipped_interactions += 1
            else:
                irows.append(row)
        con.executemany("INSERT INTO interactions VALUES (?,?,?,?)", irows)

        con.executescript(
            f"""
            CREATE VIRTUAL TABLE drugs_fts USING fts5(
                name, composition, indication, adverse_reactions,
                content='drugs', content_rowid='rowid'{fts_tokenizer}
            );
            INSERT INTO drugs_fts(drugs_fts) VALUES('rebuild');
            CREATE INDEX idx_inter_a ON interactions(a);
            CREATE INDEX idx_inter_b ON interactions(b);
            """
        )
        con.commit()
        check = con.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError(f"SQLite 完整性检查失败：{check}")
        counts = (
            con.execute("SELECT COUNT(*) FROM drugs").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM interactions").fetchone()[0],
        )
        con.close()
        con = None
        os.replace(tmp, out)
        return (
            counts[0],
            counts[1],
            skipped_drugs,
            duplicate_drugs,
            skipped_interactions,
        )
    finally:
        if con is not None:
            con.close()
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    global SRC, OUT
    configure_stdio()
    ap = argparse.ArgumentParser(description="构建本地药品库（SQLite + FTS5）")
    ap.add_argument(
        "--src", help="原始 JSON 路径（默认 $DRUGDB_SRC 或 ./drug_db_v2.json）"
    )
    ap.add_argument("--out", help="产出路径（默认 $DRUGDB 或 ~/drugdb/drugs.sqlite）")
    args = ap.parse_args()
    if args.src:
        SRC = Path(args.src).expanduser()
    if args.out:
        OUT = Path(args.out).expanduser()

    try:
        if SRC.resolve() == OUT.resolve():
            raise ValueError("--src 与 --out 不能指向同一个文件")
        drugs, interactions = load_payload(SRC)
        n_drugs, n_inter, skip_d, dup_d, skip_i = build_database(
            OUT, drugs, interactions
        )
    except (TypeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error) as exc:
        print(f"建库失败，原库未改动：{exc}", file=sys.stderr)
        return 1

    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"建成: {OUT}  ({size_mb:.1f} MB)")
    print(f"药品: {n_drugs} 条 | 相互作用: {n_inter} 条")
    if skip_d or dup_d or skip_i:
        print(
            f"跳过无效药品: {skip_d} 条 | 同名覆盖: {dup_d} 条"
            f" | 跳过无效相互作用: {skip_i} 条"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
