"""Behavior tests using isolated repositories and synthetic secrets only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("scan_git.py")
spec = importlib.util.spec_from_file_location("scan_git", SCRIPT)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class GitAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="git-audit-test-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        # Generated at runtime, never an actual provider credential.
        self.secret = "sk-" + "SyntheticCredential12345" * 2

    def git(self, *args):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME="Audit Test", GIT_AUTHOR_EMAIL="audit@example.invalid",
                   GIT_COMMITTER_NAME="Audit Test", GIT_COMMITTER_EMAIL="audit@example.invalid")
        return subprocess.check_output(
            ["git", "-C", str(self.repo), "-c", "commit.gpgsign=false", "-c",
             "core.hooksPath=" + str(self.repo / "no-hooks"), *args], env=env, stderr=subprocess.PIPE
        ).decode().strip()

    def write(self, path, content):
        (self.repo / path).write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic fixture")
        return self.git("rev-parse", "HEAD")

    def scan(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.repo), *args],
                                capture_output=True, text=True)
        self.assertNotIn(self.secret, result.stdout + result.stderr)
        return result.returncode, json.loads(result.stdout)

    def scopes(self, report, rule="provider_token"):
        return {f["scope"] for f in report["findings"] if f["rule"] == rule}

    def test_secret_removed_from_worktree_but_retained_in_history(self):
        self.write("gen-env.ps1", "LLM_API_KEY=" + self.secret)
        old = self.commit()
        self.write("gen-env.ps1", "LLM_API_KEY=your-llm-api-key")
        self.commit()
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertEqual(self.scopes(report), {"history"})
        self.assertTrue(any(f.get("example_commit") == old for f in report["findings"]))

    def test_worktree_fixed_but_index_still_leaks(self):
        self.write("config.py", 'KEY="' + self.secret + '"')
        self.git("add", "config.py")
        self.write("config.py", 'KEY="placeholder"')
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertEqual(self.scopes(report), {"index"})

    def test_ignored_env_does_not_hide_generator(self):
        self.write(".gitignore", ".env\n")
        self.write(".env", "LLM_API_KEY=" + self.secret)
        self.write("gen-env.ps1", "LLM_API_KEY=" + self.secret)
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertTrue(any(f["path"] == "gen-env.ps1" for f in report["findings"]))
        self.assertFalse(any(f["path"] == ".env" for f in report["findings"]))

    def test_clean_templates_and_ignored_secret(self):
        self.write(".gitignore", ".env\n")
        self.write(".env", "LLM_API_KEY=" + self.secret)
        self.write(".env.example", "LLM_API_KEY=your-llm-api-key\nVISION_API_KEY=\n")
        self.commit()
        code, report = self.scan()
        self.assertEqual(code, 0)
        self.assertEqual(report["findings"], [])

    def test_tracked_env_not_protected_by_ignore(self):
        self.write(".env", "LLM_API_KEY=" + self.secret)
        self.commit()
        self.write(".gitignore", ".env\n")
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertIn(".env", report["tracked_but_ignored"])
        self.assertEqual(self.scopes(report), {"worktree", "index", "history"})

    def test_tag_only_secret_is_scanned(self):
        self.write("README.md", "clean")
        clean = self.commit()
        self.write("old.txt", self.secret)
        self.commit()
        self.git("tag", "old-release")
        self.git("reset", "--hard", clean)  # Only this isolated disposable fixture.
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertEqual(self.scopes(report), {"history"})

    def test_selected_outgoing_range(self):
        self.write("old.txt", self.secret)
        base = self.commit()
        self.write("old.txt", "removed")
        self.commit()
        code, report = self.scan("--ref", "HEAD", "--base", base)
        self.assertEqual(code, 0)
        self.assertEqual(report["counts"]["commits"], 1)
        self.assertFalse(report["history_selection"]["all_local_refs"])

    def test_binary_and_oversize_fail_closed(self):
        (self.repo / "binary.dat").write_bytes(b"a\x00b")
        self.write("large.txt", "x" * 128)
        code, report = self.scan("--max-bytes", "64")
        self.assertEqual(code, 2)
        self.assertEqual({g["reason"] for g in report["gaps"]}, {"binary", "oversized"})

    def test_utf16_powershell_and_space_in_filename(self):
        (self.repo / "generate env.ps1").write_text("LLM_API_KEY=" + self.secret, encoding="utf-16")
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertEqual(self.scopes(report), {"worktree"})
        self.assertTrue(all(f["path"] == "generate env.ps1" for f in report["findings"]))

    def test_generic_secret_url_and_private_key_are_redacted(self):
        values = ["BEGIN " + "PRIVATE KEY", "nonprovidercredential123456", "randompassword123456"]
        self.write("settings.txt", "-----" + values[0] + "-----\nAPI_KEY='" + values[1]
                   + "'\npostgresql://user:" + values[2] + "@localhost/db\n")
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertTrue({"private_key", "literal_secret", "credential_url"}.issubset(
            {f["rule"] for f in report["findings"]}))
        for value in values: self.assertNotIn(value, json.dumps(report))

    def test_invalid_ref_does_not_pass_or_echo_input(self):
        code, report = self.scan("--ref", self.secret)
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "error")

    def test_shallow_history_is_incomplete(self):
        self.write("README.md", "clean")
        head = self.commit()
        (self.repo / ".git" / "shallow").write_text(head + "\n", encoding="ascii")
        code, report = self.scan()
        self.assertEqual(code, 2)
        self.assertIn("shallow_clone", {g["reason"] for g in report["gaps"]})


if __name__ == "__main__":
    unittest.main()
