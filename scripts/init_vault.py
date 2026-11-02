#!/usr/bin/env python3
"""建一个空的 Obsidian 病历库（目录 + 表头 + 词表 + 铭牌），并 git init。

用法：
    python3 init_vault.py --dest ~/Obsidian/电子病历库 --member 〈姓名〉
    python3 init_vault.py --dest 〈路径〉 --member 〈姓名〉 --no-git
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKELETON = SKILL_ROOT / "assets" / "skeleton"
MEMBER_TOKEN = "{{成员}}"

# 这些名字在 Windows 上会映射到设备，或让跨平台同步后的路径失效。
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
WINDOWS_INVALID_CHARS = set('<>:"/\\|?*')


def configure_stdio() -> None:
    """Windows 重定向到管道时也固定用 UTF-8，避免中文输出乱码。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


PLAQUE = """# {member} 的家庭病历数据库

Obsidian 库根。**这里只存数据**：检查报告 / 就医记录 / 指标 / 用药 / 体态 / 药品库存 / 血糖监测，外加一份受控词表。
本文件是铭牌，不含归档规矩。

## 结构

```
{name}/
├── AGENTS.md                    本文件（铭牌）
├── 8-知识库/zz-项目名规范.md        受控词表（项目名 / 指标名 / 机构名 / 体重管理指标名 / 药品管理类 / 血糖监测类）
└── {member}/
    ├── 1-基本信息/ 2-检查报告/ 3-不适记录/ 4-就医记录/
    ├── 5-指标/ 6-用药/ 7-视图/ 8-体重管理/ 9-药品管理/ 10-血糖数据/
    └── attachments/
```

## 规矩在哪

所有口径（怎么写、怎么判断、字段格式、命名规范、各目录放什么）都在 **health-vault 技能**里 —— 本库只有数据，不含规矩也不含说明文件。

- **Hermes Agent**：技能已装载（`~/.hermes/skills/health/health-vault/`），按技能干活；cwd 在库内直接写，库外先问。
- **其他 agent（Codex / Cursor / Claude Code / Copilot…）**：先取到技能本体（仓库根就是技能根：`SKILL.md` + `references/` + `templates/` + `scripts/`），读 `SKILL.md` 后按它干活；本库的目录与列格式与技能一致。
- **没有这个技能时：不要直接改本库文件** —— 先把技能装上或读进来。

## 成员

当前：{member}。新增成员 = 库根并排建一个同名目录（结构同上），不要复制 `8-知识库/` 与技能。
"""


def validate_member(member: str) -> str:
    """成员名必须是一个可跨平台同步的安全目录名，而不是路径。"""
    if not member or member != member.strip():
        raise ValueError("成员名不能为空，也不能在首尾带空白")
    if member in {".", ".."}:
        raise ValueError("成员名不能是 . 或 ..")
    if any(ch in WINDOWS_INVALID_CHARS or ord(ch) < 32 for ch in member):
        raise ValueError('成员名不能包含路径分隔符或这些字符：< > : " | ? *')
    if member.endswith((".", " ")):
        raise ValueError("成员名不能以句点或空格结尾")
    if member.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        raise ValueError("成员名不能使用 Windows 保留设备名（如 CON、NUL、COM1）")
    return member


def git_run(
    args: list[str], cwd: pathlib.Path
) -> subprocess.CompletedProcess[str] | None:
    """运行 git；未安装时返回 None，由调用方降级处理。"""
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"⚠️ 无法启动 git：{exc}", file=sys.stderr)
        return None


def ensure_identity(dest: pathlib.Path) -> None:
    """仓库级 git 身份兜底：本机没有全局身份时，commit 会直接失败。"""
    who = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    for key, val in (("user.name", who), ("user.email", f"{who}@localhost")):
        have = git_run(["config", "--get", key], dest)
        if have is None:
            return
        if not have.stdout.strip():
            git_run(["config", "--local", key, val], dest)


def write_new_text(path: pathlib.Path, text: str) -> bool:
    """只创建新文件；即使并发运行也不覆盖已有病历文件。"""
    try:
        with path.open("x", encoding="utf-8", newline="") as fh:
            fh.write(text)
    except FileExistsError:
        return False
    return True


def raise_walk_error(error: OSError) -> None:
    raise error


def add_created_files(dest: pathlib.Path, created: list[pathlib.Path]) -> None:
    if not created:
        print("没有新建文件，跳过 git add / commit")
        return

    init = git_run(["init", "-q"], dest)
    if init is None:
        print("⚠️ 未找到 git，已跳过版本库初始化（数据文件已正常写入）")
        return
    if init.returncode != 0:
        detail = init.stderr.strip() or "未知错误"
        print(f"⚠️ git init 未成功（{detail}），已跳过提交")
        return

    ensure_identity(dest)
    rels = [str(path.relative_to(dest)) for path in created]
    added = git_run(["add", "--", *rels], dest)
    if added is None or added.returncode != 0:
        detail = added.stderr.strip() if added and added.stderr.strip() else "未知错误"
        print(f"⚠️ git add 未成功（{detail}）：文件已铺好，稍后手工提交")
        return

    # 显式列出本次新建的文件，避免把目标目录里原本已暂存的无关内容一起提交。
    cp = git_run(["commit", "-q", "-m", "初始化病历库骨架", "--", *rels], dest)
    if cp is not None and cp.returncode == 0:
        print("git 已初始化并提交")
        return
    detail = (
        cp.stderr.strip().splitlines()[-1] if cp and cp.stderr.strip() else "未知错误"
    )
    print(f"⚠️ git commit 未成功（{detail}）：文件已铺好，稍后手工提交")


def main() -> int:
    configure_stdio()
    ap = argparse.ArgumentParser(description="建一个空的病历库")
    ap.add_argument("--dest", required=True, help="库路径，如 ~/Obsidian/电子病历库")
    ap.add_argument(
        "--member",
        required=True,
        help="成员姓名（成员目录名）；必须是单个目录名，不能包含路径分隔符",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="目标非空也继续；不会覆盖任何已存在文件（含 AGENTS.md）",
    )
    ap.add_argument("--no-git", action="store_true", help="不做 git init")
    ap.add_argument(
        "--plaque-only",
        action="store_true",
        help="只重写铭牌 AGENTS.md（这是唯一允许覆盖铭牌的选项）",
    )
    a = ap.parse_args()

    try:
        member = validate_member(a.member)
    except ValueError as exc:
        ap.error(str(exc))

    dest = pathlib.Path(a.dest).expanduser().resolve()
    if dest.exists() and not dest.is_dir():
        print(f"目标路径不是目录：{dest}", file=sys.stderr)
        return 1

    if a.plaque_only:
        try:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "AGENTS.md").write_text(
                PLAQUE.format(member=member, name=dest.name), encoding="utf-8"
            )
        except OSError as exc:
            print(f"写入铭牌失败：{exc}", file=sys.stderr)
            return 1
        print(f"铭牌已写：{dest / 'AGENTS.md'}")
        return 0
    if dest.exists() and not a.force:
        try:
            nonempty = any(dest.iterdir())
        except OSError as exc:
            print(f"无法读取目标目录：{exc}", file=sys.stderr)
            return 1
        if nonempty:
            print(
                f"目标已存在且非空：{dest}\n"
                f"往已有库里补骨架请加 --force（不会覆盖任何已存在文件）；"
                f"只想重写铭牌用 --plaque-only。",
                file=sys.stderr,
            )
            return 1
    if not SKELETON.is_dir():
        print(f"找不到空骨架：{SKELETON}", file=sys.stderr)
        return 1

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"无法创建库目录：{exc}", file=sys.stderr)
        return 1

    copied = skipped = 0
    created: list[pathlib.Path] = []
    try:
        for dp, _dirs, files in os.walk(SKELETON, onerror=raise_walk_error):
            rel = pathlib.Path(dp).relative_to(SKELETON)
            parts = [member if part == MEMBER_TOKEN else part for part in rel.parts]
            target_dir = dest.joinpath(*parts) if parts else dest
            target_dir.mkdir(parents=True, exist_ok=True)

            for filename in files:
                if filename == ".gitkeep":  # 占位文件不带进用户的库
                    continue
                src_file = pathlib.Path(dp) / filename
                target = target_dir / filename
                if target.exists():  # 已存在一律不覆盖
                    skipped += 1
                    continue

                try:
                    text = src_file.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    shutil.copy2(src_file, target)  # 二进制文件原样复制
                    wrote = True
                except OSError as exc:
                    print(f"读取骨架失败：{src_file}（{exc}）", file=sys.stderr)
                    return 1
                else:
                    wrote = write_new_text(target, text.replace(MEMBER_TOKEN, member))

                if wrote:
                    created.append(target)
                    copied += 1
                else:
                    skipped += 1

        plaque = dest / "AGENTS.md"
        if plaque.exists():
            skipped += 1
            print("铭牌 AGENTS.md 已存在，未覆盖（要重写请用 --plaque-only）")
        elif write_new_text(plaque, PLAQUE.format(member=member, name=dest.name)):
            created.append(plaque)
            print("铭牌已写：AGENTS.md")
        else:
            skipped += 1
    except OSError as exc:
        print(f"铺骨架失败：{exc}", file=sys.stderr)
        return 1

    print(
        f"骨架已铺好：{dest}（写入 {copied} 个文件"
        + (f"，跳过已存在的 {skipped} 个" if skipped else "")
        + "）"
    )

    if not a.no_git:
        add_created_files(dest, created)

    print(f"\n下一步：用 Obsidian 打开库根 → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
