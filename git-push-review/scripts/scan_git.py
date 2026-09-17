"""Read-only, redacted Git secret triage. Not a complete security audit.

Exit 0: no rule hits or coverage gaps; 1: findings; 2: gaps/error (may also
contain findings). JSON goes to stdout. No network or credential validation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


RULES = {
    "provider_token": re.compile(
        r"\b(?:sk-(?:lf-)?[A-Za-z0-9_-]{16,}|pk-lf-[A-Za-z0-9_-]{16,}"
        r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,}"
        r"|xox[baprs]-[A-Za-z0-9-]{20,})"
    ),
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}"),
    "credential_url": re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.I),
    "literal_secret": re.compile(
        r'''(?im)\b(?:[a-z0-9_]*(?:api[_-]?key|secret[_-]?key|password|access[_-]?token|auth[_-]?token))'''
        r'''["']?\s*(?::\s*str\s*)?[:=]\s*["']([^"'\r\n]{8,})["']'''
    ),
    "env_secret": re.compile(
        r"(?im)^\s*[A-Z0-9_]*(?:API_KEY|SECRET_KEY|PASSWORD|ACCESS_TOKEN|AUTH_TOKEN)"
        r"\s*=\s*([^\s\"'#$][^\s\r\n]{7,})"
    ),
}


class AuditError(Exception):
    pass


def git(repo, *args, optional=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1",
               GIT_OPTIONAL_LOCKS="0")
    p = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=env, timeout=120)
    if p.returncode and not optional:
        # Never relay raw stderr: it can contain credential-bearing URLs.
        raise AuditError("git_command_failed:" + args[0])
    return p.stdout if p.returncode == 0 else None


def decode_path(raw):
    return raw.decode("utf-8", errors="surrogateescape")


def placeholder(value):
    return bool(re.fullmatch(r"(?:your-[a-z-]+|<[^>]+>|\$\{[^}]+\}|sk-xxx|change-me-in-production)", value))


def audit(repo, ref=None, base=None, max_bytes=5_000_000):
    repo = Path(repo).resolve()
    root = git(repo, "rev-parse", "--show-toplevel").decode().strip()
    repo = Path(root)
    report = {"version": 1, "status": "", "findings": [], "gaps": [],
              "counts": {"worktree": 0, "index": 0, "history": 0, "commits": 0},
              "coverage": "local worktree + index + selected reachable commit trees",
              "limits": ["rule-based triage, not proof of absence", "no remote fetch or key validation",
                         "README semantics require manual review", "unreachable objects and reflogs excluded"]}
    inventory = hashlib.sha256()
    seen = set()
    blob_cache = {}

    def gap(scope, path, reason):
        report["gaps"].append({"scope": scope, "path": path, "reason": reason})

    def scan(data, scope, path, oid=None, commit=None):
        identity = (scope, path, oid)
        if oid and identity in seen:
            return
        seen.add(identity)
        report["counts"][scope] += 1
        inventory.update(json.dumps([scope, path, oid, hashlib.sha256(data).hexdigest()]).encode())
        if len(data) > max_bytes:
            gap(scope, path, "oversized"); return
        try:
            text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
        except UnicodeError:
            gap(scope, path, "binary_or_unsupported_encoding"); return
        if "\x00" in text:
            gap(scope, path, "binary"); return
        if text.startswith("version https://git-lfs.github.com/spec/v1"):
            gap(scope, path, "lfs_payload_not_scanned")
        for rule, pattern in RULES.items():
            for match in pattern.finditer(text):
                if rule in ("literal_secret", "env_secret") and placeholder(match[1]):
                    continue
                finding = {"scope": scope, "path": path, "line": text.count("\n", 0, match.start()) + 1,
                           "rule": rule}
                if oid: finding["blob"] = oid
                if commit: finding["example_commit"] = commit
                report["findings"].append(finding)

    def blob(oid, scope, path, commit=None):
        if oid not in blob_cache:
            size = int(git(repo, "cat-file", "-s", oid))
            blob_cache[oid] = None if size > max_bytes else git(repo, "cat-file", "blob", oid)
        if blob_cache[oid] is None:
            gap(scope, path, "oversized_blob")
        else:
            scan(blob_cache[oid], scope, path, oid, commit)

    paths = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for raw in sorted(set(paths.split(b"\0")) - {b""}):
        path = decode_path(raw)
        target = repo / path
        # Do not follow symlinks/junctions out of or within the workspace.
        parts = [target, *list(target.parents)[:len(Path(path).parts)-1]]
        if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in parts):
            gap("worktree", path, "symlink_or_junction"); continue
        if not target.exists():  # A tracked deletion is still covered in index/history.
            continue
        if not target.is_file():
            gap("worktree", path, "non_regular_file"); continue
        if target.stat().st_size > max_bytes:
            gap("worktree", path, "oversized"); continue
        scan(target.read_bytes(), "worktree", path)

    for row in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if not row: continue
        meta, raw = row.split(b"\t", 1)
        mode, oid, stage = meta.decode().split()
        path = decode_path(raw)
        if mode not in ("100644", "100755"):
            gap("index", path, "submodule_or_symlink"); continue
        if stage != "0": gap("index", path, "unmerged_index")
        blob(oid, "index", path)

    if git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
        gap("history", "", "shallow_clone")
    if base and not ref:
        raise AuditError("base_requires_ref")

    def resolve(value):
        return git(repo, "rev-parse", "--verify", "--end-of-options", value + "^{commit}").decode().strip()

    tip = resolve(ref) if ref else None
    baseline = resolve(base) if base else None
    report["history_selection"] = {"tip": tip, "base": baseline, "all_local_refs": not bool(ref)}
    commits = git(repo, "rev-list", tip, "^" + baseline) if baseline else git(repo, "rev-list", tip or "--all")
    for commit in commits.decode().splitlines():
        report["counts"]["commits"] += 1
        for row in git(repo, "ls-tree", "-r", "-z", commit).split(b"\0"):
            if not row: continue
            meta, raw = row.split(b"\t", 1)
            mode, kind, oid = meta.decode().split()
            path = decode_path(raw)
            if kind != "blob" or mode == "120000":
                gap("history", path, "submodule_or_symlink"); continue
            blob(oid, "history", path, commit)
    ignored = git(repo, "ls-files", "-z", "--cached", "--ignored", "--exclude-standard")
    report["tracked_but_ignored"] = [decode_path(x) for x in ignored.split(b"\0") if x]
    for path in report["tracked_but_ignored"]:
        report["findings"].append({"scope": "index", "path": path, "rule": "tracked_but_ignored"})
    report["inventory_sha256"] = inventory.hexdigest()
    report["status"] = "incomplete" if report["gaps"] else "findings" if report["findings"] else "no_rule_hits"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--ref", help="Commit/branch/tag to inspect; default: all local refs")
    parser.add_argument("--base", help="Exclude commits reachable from this verified remote baseline; requires --ref")
    parser.add_argument("--max-bytes", type=int, default=5_000_000)
    args = parser.parse_args()
    try:
        if args.max_bytes < 1: raise AuditError("invalid_max_bytes")
        report = audit(args.repo, args.ref, args.base, args.max_bytes)
    except (AuditError, OSError, ValueError, subprocess.SubprocessError) as exc:
        # No traceback or exception values: paths/commands may contain secrets.
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 2 if report["gaps"] else 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
