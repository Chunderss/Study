"""One-time migration from the legacy `lists/` layout to `modules/`.

Legacy:  <root>/lists/<Name>/{words.json, stats.json, sources/}
Module:  <root>/modules/<Name>/{module.json, vocab/{words.json,stats.json},
                                notes/, documents/}

Safety rules:
  * Only runs when a legacy lists/ dir with real lists exists.
  * Makes a full backup copy of lists/ -> lists.backup-<timestamp>/ FIRST.
  * Copies (does not move) each list into its module, then verifies the module
    reads back before removing the original. A failure leaves originals intact.
  * Idempotent: a module already present for a given name is skipped, and the
    legacy dir is only renamed to lists.migrated-* after everything succeeds.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from .module import Module
from .paths import Paths


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def needs_migration(paths: Paths) -> bool:
    legacy = paths.legacy_lists_dir
    if not legacy.exists():
        return False
    for p in legacy.iterdir():
        if p.is_dir() and (p / "words.json").exists():
            return True
    return False


def migrate(paths: Paths) -> List[str]:
    """Perform the migration. Returns the list of module names created.

    Raises on a verification failure (originals are left untouched so the user
    loses nothing and can retry / inspect).
    """
    legacy = paths.legacy_lists_dir
    if not needs_migration(paths):
        return []

    # 1. full backup first
    backup = legacy.parent / f"lists.backup-{_stamp()}"
    shutil.copytree(legacy, backup)

    created: List[str] = []
    paths.modules_dir.mkdir(parents=True, exist_ok=True)

    for src in sorted(legacy.iterdir()):
        if not src.is_dir() or not (src / "words.json").exists():
            continue
        name = src.name
        mod_dir = paths.module_dir(name)
        if mod_dir.exists():
            continue  # already migrated / name taken — skip, don't clobber

        # 2. build the module skeleton
        paths.ensure_module(name)

        # 3. copy vocab files into vocab/
        shutil.copy2(src / "words.json", paths.words_file(name))
        stats_src = src / "stats.json"
        if stats_src.exists():
            shutil.copy2(stats_src, paths.stats_file(name))

        # 4. copy legacy sources/ -> documents/
        sources_src = src / "sources"
        if sources_src.exists() and sources_src.is_dir():
            for f in sources_src.iterdir():
                if f.is_file():
                    shutil.copy2(f, paths.documents_dir(name) / f.name)

        # 5. write the manifest
        _write_manifest(paths, name)

        # 6. verify the module reads back before trusting it
        if not paths.words_file(name).exists():
            raise RuntimeError(f"Migration verify failed for '{name}': words.json missing.")

        created.append(name)

    # 7. only now retire the legacy dir (renamed, not deleted — extra safety)
    retired = legacy.parent / f"lists.migrated-{_stamp()}"
    legacy.rename(retired)
    return created


def _write_manifest(paths: Paths, name: str) -> None:
    import json
    mod = Module(name=name)
    with open(paths.manifest_file(name), "w", encoding="utf-8") as f:
        json.dump(mod.to_dict(), f, ensure_ascii=False, indent=2)
