from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
INIT = ROOT / "scripts" / "init_vault.py"
BUILD = ROOT / "scripts" / "drugdb" / "build.py"
QUERY = ROOT / "scripts" / "drugdb" / "q.py"


def run_script(script: Path, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [PYTHON, str(script), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def git(*args: str, cwd: Path):
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class InitVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_creates_skeleton_and_replaces_member_placeholder(self) -> None:
        dest = self.root / "vault"
        result = run_script(
            INIT, "--dest", str(dest), "--member", "测试成员", "--no-git"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        basic = dest / "测试成员" / "1-基本信息" / "基本信息.md"
        self.assertTrue(basic.is_file())
        self.assertIn("测试成员", basic.read_text(encoding="utf-8"))
        self.assertTrue((dest / "测试成员" / "10-血糖数据").is_dir())
        self.assertFalse(list(dest.rglob("*{{成员}}*")))
        for path in dest.rglob("*"):
            if path.is_file():
                self.assertNotIn(
                    "{{成员}}", path.read_text(encoding="utf-8", errors="ignore")
                )

    def test_rejects_member_path_traversal(self) -> None:
        dest = self.root / "vault"
        result = run_script(
            INIT, "--dest", str(dest), "--member", "..\\escaped", "--no-git"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "escaped").exists())
        self.assertFalse(dest.exists())

    def test_rejects_windows_reserved_member_name(self) -> None:
        result = run_script(
            INIT, "--dest", str(self.root / "vault"), "--member", "CON.txt", "--no-git"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("保留设备名", result.stderr)

    def test_force_never_overwrites_existing_files(self) -> None:
        dest = self.root / "vault"
        dest.mkdir()
        plaque = dest / "AGENTS.md"
        unrelated = dest / "unrelated.txt"
        plaque.write_text("USER CUSTOM PLAQUE", encoding="utf-8")
        unrelated.write_text("UNRELATED", encoding="utf-8")

        result = run_script(
            INIT, "--dest", str(dest), "--member", "成员", "--force", "--no-git"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(plaque.read_text(encoding="utf-8"), "USER CUSTOM PLAQUE")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "UNRELATED")
        self.assertTrue((dest / "成员" / "7-视图" / "主档案.md").is_file())

    def test_plaque_only_is_explicit_overwrite(self) -> None:
        dest = self.root / "vault"
        dest.mkdir()
        plaque = dest / "AGENTS.md"
        plaque.write_text("OLD", encoding="utf-8")
        result = run_script(
            INIT, "--dest", str(dest), "--member", "成员", "--plaque-only"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = plaque.read_text(encoding="utf-8")
        self.assertIn("成员 的家庭病历数据库", text)
        self.assertNotEqual(text, "OLD")

    def test_rejects_destination_file(self) -> None:
        dest = self.root / "vault"
        dest.write_text("not a directory", encoding="utf-8")
        result = run_script(INIT, "--dest", str(dest), "--member", "成员", "--no-git")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不是目录", result.stderr)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_git_commit_does_not_include_preexisting_or_staged_files(self) -> None:
        dest = self.root / "vault"
        dest.mkdir()
        (dest / "AGENTS.md").write_text("USER CUSTOM PLAQUE", encoding="utf-8")
        (dest / "unrelated.txt").write_text("UNRELATED", encoding="utf-8")
        self.assertEqual(git("init", "-q", cwd=dest).returncode, 0)
        self.assertEqual(git("config", "user.name", "test", cwd=dest).returncode, 0)
        self.assertEqual(
            git("config", "user.email", "test@example.invalid", cwd=dest).returncode, 0
        )
        self.assertEqual(git("add", "unrelated.txt", cwd=dest).returncode, 0)

        result = run_script(INIT, "--dest", str(dest), "--member", "成员", "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        tree = git("ls-tree", "-r", "--name-only", "HEAD", cwd=dest)
        self.assertEqual(tree.returncode, 0, tree.stderr)
        names = tree.stdout.splitlines()
        self.assertIn("成员/1-基本信息/基本信息.md", names)
        self.assertNotIn("unrelated.txt", names)
        self.assertNotIn("AGENTS.md", names)


class DrugDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "drugs.json"
        self.database = self.root / "drugs.sqlite"
        self.payload = {
            "drugs": {
                "布洛芬缓释胶囊": {
                    "composition": "布洛芬",
                    "indication": "用于缓解轻至中度疼痛，如头痛。",
                    "contraindications": "对本品过敏者禁用。",
                    "adverse_reactions": "恶心、皮疹。",
                    "drug_interactions": "与阿司匹林合用需咨询医生。",
                    "dosage": "遵医嘱。",
                    "approval_number": "国药准字H00000001",
                },
                "阿司匹林肠溶片": {
                    "composition": "阿司匹林",
                    "indication": "用于解热镇痛。",
                    "contraindications": "活动性消化性溃疡禁用。",
                },
                "0.9%氯化钠注射液": {
                    "composition": "氯化钠",
                    "indication": "补液。",
                },
            },
            "interactions": [
                {
                    "pair": ["阿司匹林", "华法林"],
                    "risk": "high",
                    "note": "增加出血风险。",
                },
                {"unrecognized": "ignored"},
            ],
        }
        self.source.write_text(
            json.dumps(self.payload, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self, *extra: str):
        return run_script(
            BUILD, "--src", str(self.source), "--out", str(self.database), *extra
        )

    def query(self, *args: str):
        return run_script(QUERY, "--db", str(self.database), *args)

    def test_build_and_name_only_includes_composition(self) -> None:
        built = self.build()
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertIn("药品: 3 条 | 相互作用: 1 条", built.stdout)
        result = self.query("-n", "布洛芬")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("布洛芬缓释胶囊", result.stdout)
        self.assertIn("成分：布洛芬", result.stdout)
        approval = self.query("--field", "批准文号", "布洛芬")
        self.assertEqual(approval.returncode, 0, approval.stderr)
        self.assertIn("国药准字H00000001", approval.stdout)

    def test_short_chinese_full_text_search_falls_back_to_like(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        result = self.query("-s", "头痛")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("布洛芬缓释胶囊", result.stdout)

    def test_search_special_characters_is_literal(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        quoted = self.query("-s", '" OR *')
        self.assertEqual(quoted.returncode, 0, quoted.stderr)
        percent = self.query("0.9%氯化钠")
        self.assertEqual(percent.returncode, 0, percent.stderr)
        self.assertIn("0.9%氯化钠注射液", percent.stdout)
        wildcard_prefix = self.query("%氯化")
        self.assertEqual(wildcard_prefix.returncode, 0, wildcard_prefix.stderr)
        self.assertIn("0.9%氯化钠注射液", wildcard_prefix.stdout)

    def test_interaction_lookup(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        result = self.query("-i", "阿司匹林", "华法林")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("增加出血风险", result.stdout)
        self.assertIn("[high]", result.stdout)

    def test_invalid_field_and_limit_are_rejected(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        field = self.query("--field", "不存在", "布洛芬")
        self.assertEqual(field.returncode, 2)
        limit = self.query("-s", "布洛芬", "--limit", "0")
        self.assertEqual(limit.returncode, 2)

    def test_no_args_prints_help_without_opening_database(self) -> None:
        result = self.query()
        self.assertEqual(result.returncode, 1)
        self.assertIn("usage:", result.stdout)
        self.assertNotIn("药品库不存在", result.stdout + result.stderr)

    def test_failed_rebuild_preserves_existing_database(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        with closing(sqlite3.connect(self.database)) as con:
            con.execute("CREATE TABLE marker(value TEXT)")
            con.execute("INSERT INTO marker VALUES ('keep-me')")
            con.commit()
        self.source.write_text("{ invalid json", encoding="utf-8")
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        with closing(sqlite3.connect(self.database)) as con:
            value = con.execute("SELECT value FROM marker").fetchone()[0]
        self.assertEqual(value, "keep-me")

    def test_tilde_paths_are_expanded(self) -> None:
        env = os.environ.copy()
        env["DRUGDB"] = "~/drugs.sqlite"
        env["HOME"] = str(self.root)
        env["USERPROFILE"] = str(self.root)
        built = run_script(
            BUILD, "--src", str(self.source), "--out", "~/drugs.sqlite", env=env
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertTrue((self.root / "drugs.sqlite").is_file())
        queried = run_script(QUERY, "-n", "布洛芬", env=env)
        self.assertEqual(queried.returncode, 0, queried.stderr)
        self.assertIn("成分：布洛芬", queried.stdout)

    def test_source_json_accepts_utf8_bom(self) -> None:
        text = json.dumps(self.payload, ensure_ascii=False)
        self.source.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.database.is_file())

    def test_non_utf8_source_fails_without_traceback(self) -> None:
        self.source.write_bytes(b"\xff\xfe\x00")
        result = self.build()
        self.assertEqual(result.returncode, 1)
        self.assertIn("UTF-8", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_source_and_output_cannot_be_the_same_file(self) -> None:
        original = self.source.read_bytes()
        result = run_script(BUILD, "--src", str(self.source), "--out", str(self.source))
        self.assertEqual(result.returncode, 1)
        self.assertIn("不能指向同一个文件", result.stderr)
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
