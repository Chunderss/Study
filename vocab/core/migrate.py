"""One-time migration from the legacy `lists/` layout to `modules/`.

Legacy:  <root>/lists/<Name>/{words.json, stats.json, sources/}
Module:  <root>/modules/<Name>/{module.json, vocab/{words.json,stats.json},
                                notes/, documents/}

Safety rules:
  * Only runs when a legacy lists/ dir with real lists exists.
  * Makes a full backup copy of lists/ -> lists.backup-<timestamp>/ FIRST.
  * Builds and verifies each module outside modules/, then publishes it with
    one directory rename. A failure leaves originals intact.
  * Only skips existing modules with matching completed-migration metadata.
    Conflicts keep lists/ pending and never overwrite an existing module.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from .module import Module
from .paths import Paths
from .storage import Storage, _atomic_write


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _archive_path(legacy: Path, kind: str) -> Path:
    # Retries can happen within the same second, including after backup failure.
    return legacy.parent / f"lists.{kind}-{_stamp()}-{uuid.uuid4().hex}"


def _legacy_lists(directory: Path):
    return sorted(p for p in directory.iterdir()
                  if p.is_dir() and (p / "words.json").exists())


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(source: Path) -> dict:
    files = [source / "words.json"]
    if (source / "stats.json").exists():
        files.append(source / "stats.json")
    if (source / "sources").is_dir():
        files.extend(p for p in (source / "sources").rglob("*") if p.is_file())
    return {p.relative_to(source).as_posix(): _digest(p) for p in sorted(files)}


def _provenance(name: str, fingerprint: dict) -> dict:
    return {"schema": 1, "source": name, "files": fingerprint}


def _already_migrated(destination: Path, name: str, fingerprint: dict) -> bool:
    try:
        with (destination / "module.json").open(encoding="utf-8") as stream:
            manifest = json.load(stream)
        metadata = manifest.get("metadata", {})
        return metadata.get("legacy_migration") == _provenance(name, fingerprint)
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def _verify(paths: Paths, name: str, fingerprint: dict) -> None:
    for relative, expected in fingerprint.items():
        source_path = Path(relative)
        if source_path.parts[0] == "sources":
            destination = paths.documents_dir(name).joinpath(*source_path.parts[1:])
        else:
            destination = paths.vocab_dir(name) / source_path
        if _digest(destination) != expected:
            raise RuntimeError(f"Migration verify failed for '{name}': {relative} changed.")
    storage = Storage(paths)
    storage.load_words(name)
    stats = storage.load_stats(name)
    if not isinstance(stats, dict) or not all(isinstance(card, dict) for card in stats.values()):
        raise ValueError(f"Migration verify failed for '{name}': invalid stats.json.")


def needs_migration(paths: Paths) -> bool:
    legacy = paths.legacy_lists_dir
    if not legacy.exists():
        return False
    return bool(_legacy_lists(legacy))


def migrate(paths: Paths) -> List[str]:
    """Perform the migration. Returns the list of module names created.

    Raises on a verification failure (originals are left untouched so the user
    loses nothing and can retry / inspect).
    """
    legacy = paths.legacy_lists_dir
    if not needs_migration(paths):
        return []

    # 1. full backup first
    backup = _archive_path(legacy, "backup")
    shutil.copytree(legacy, backup)

    created: List[str] = []
    conflicts: List[str] = []
    paths.modules_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {src.name: _fingerprint(src) for src in _legacy_lists(backup)}

    for name, fingerprint in snapshot.items():
        src = backup / name
        mod_dir = paths.module_dir(name)
        if mod_dir.exists() or mod_dir.is_symlink():
            if not _already_migrated(mod_dir, name, fingerprint):
                conflicts.append(name)
            continue

        # A crash may leave staging files behind, but they cannot appear in the
        # module picker or be mistaken for a completed migration on retry.
        with tempfile.TemporaryDirectory(prefix=".lists-migration-", dir=paths.root) as temporary:
            staged = Paths(Path(temporary))
            staged.ensure_module(name)
            shutil.copy2(src / "words.json", staged.words_file(name))
            if (src / "stats.json").exists():
                shutil.copy2(src / "stats.json", staged.stats_file(name))
            if (src / "sources").is_dir():
                shutil.copytree(src / "sources", staged.documents_dir(name), dirs_exist_ok=True)
            _write_manifest(staged, name, fingerprint)
            _verify(staged, name, fingerprint)
            if mod_dir.exists() or mod_dir.is_symlink():
                raise RuntimeError(f"Migration stopped: module '{name}' appeared while copying.")
            staged.module_dir(name).rename(mod_dir)

        created.append(name)

    if conflicts:
        raise RuntimeError(
            "Migration needs attention: existing module(s) have no matching completed "
            f"migration: {', '.join(conflicts)}. Existing modules and lists/ were kept "
            "unchanged; resolve the name conflicts before retrying.")
    current = {src.name: _fingerprint(src) for src in _legacy_lists(legacy)}
    if current != snapshot:
        raise RuntimeError("Legacy lists changed during migration; lists/ was kept for recovery.")

    # Only retire the legacy directory after every list has a completed target.
    retired = _archive_path(legacy, "migrated")
    legacy.rename(retired)
    return created


def _write_manifest(paths: Paths, name: str, fingerprint: dict) -> None:
    mod = Module(name=name, metadata={"legacy_migration": _provenance(name, fingerprint)})
    _atomic_write(paths.manifest_file(name), mod.to_dict())
