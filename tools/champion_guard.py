#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backup/list/restore guard for MeituanRSD_autosolver.

Every training round should call backup first. The archive contains a manifest with:
created_at, round, note, files, sha256, size, and project name.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups" / "legacy"
FILES = [
    "submission/solver.py",
    "solver.py",
    "core_solver.py",
    "solver_submission_standalone.py",
    "memory/studio/current_state.json",
    "memory/studio/trials.jsonl",
    "memory/studio/chat.jsonl",
    "memory/studio/agent_logs.jsonl",
    "memory/studio/champion_registry.json",
    "config/training_config.json",
    "config/seed_config_large_seed301.json",
    "docs/Notes.md",
    "docs/Handover.md",
    "logs/studio/training_rounds.jsonl",
]


def ts_file():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def ts_iso():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def infer_round() -> int:
    state = ROOT / "memory" / "studio" / "current_state.json"
    if not state.exists():
        return 0
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
        return int((data.get("candidate") or {}).get("round") or data.get("round") or 0)
    except Exception:
        return 0


def backup(args):
    BACKUPS.mkdir(exist_ok=True)
    round_no = args.round if args.round is not None else infer_round()
    safe_tag = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in args.tag)
    name = f"{safe_tag}_r{round_no}_{ts_file()}.zip"
    out = BACKUPS / name
    manifest = {
        "project": "MeituanRSD_autosolver",
        "created_at": ts_iso(),
        "round": round_no,
        "tag": args.tag,
        "note": args.note,
        "files": []
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in FILES:
            p = ROOT / rel
            if p.exists():
                z.write(p, rel)
                manifest["files"].append({
                    "path": rel,
                    "sha256": sha(p),
                    "size": p.stat().st_size,
                    "modified_at": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                })
        z.writestr("BACKUP_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(str(out.relative_to(ROOT)))


def inspect_archive(path: Path):
    item = {
        "archive": path.name,
        "created_at": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "size_kb": round(path.stat().st_size / 1024, 2),
        "round": "--",
        "tag": "",
        "note": "",
        "solver_hash": "--",
        "files": []
    }
    try:
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("BACKUP_MANIFEST.json").decode("utf-8"))
            item.update({k: manifest.get(k, item.get(k)) for k in ["created_at", "round", "tag", "note"]})
            item["files"] = manifest.get("files", [])
            for f in item["files"]:
                if f.get("path") == "submission/solver.py":
                    item["solver_hash"] = f.get("sha256", "--")[:16]
                    break
    except Exception as e:
        item["note"] = f"manifest read failed: {e}"
    return item


def list_(args):
    BACKUPS.mkdir(exist_ok=True)
    items = [inspect_archive(p) for p in sorted(BACKUPS.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:args.limit]]
    if args.json:
        print(json.dumps({"items": items}, ensure_ascii=False, indent=2))
    else:
        for item in items:
            print(f"{item['archive']} | time={item['created_at']} | round={item['round']} | solver_sha={item['solver_hash']} | note={item['note']}")


def restore(args):
    p = Path(args.archive)
    if not p.exists():
        p = BACKUPS / args.archive
    if not p.exists():
        raise FileNotFoundError(args.archive)

    class A:
        tag = "pre_restore"
        note = f"auto pre-restore before restoring {p.name}"
        round = infer_round()
    backup(A())

    with zipfile.ZipFile(p) as z:
        for name in z.namelist():
            if name.endswith("/") or name == "BACKUP_MANIFEST.json":
                continue
            target = ROOT / name
            # Only restore known project files, never arbitrary paths.
            if name not in FILES and not name.startswith("config/seed_config_"):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    print("restored", p.name)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--tag", default="snapshot")
    b.add_argument("--note", default="")
    b.add_argument("--round", type=int, default=None)
    b.set_defaults(func=backup)

    l = sub.add_parser("list")
    l.add_argument("--limit", type=int, default=20)
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=list_)

    r = sub.add_parser("restore")
    r.add_argument("--archive", required=True)
    r.set_defaults(func=restore)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
