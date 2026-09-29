import json
from pathlib import Path

import pytest

from vocab.core import migrate
from vocab.core.models import Sense, Word
from vocab.core.paths import get_paths
from vocab.core.storage import Storage


def legacy_list(paths, name="Book"):
    source = paths.legacy_lists_dir / name
    (source / "sources").mkdir(parents=True)
    (source / "words.json").write_text(json.dumps({
        "schema": 1, "name": name,
        "words": {"word": {"senses": [{"definition": "meaning"}]}},
    }), encoding="utf-8")
    (source / "stats.json").write_text(json.dumps({
        "word": {"algo": "leitner", "box": 4, "due": 12345, "reps": 7},
    }), encoding="utf-8")
    (source / "sources" / "reference.pdf").write_bytes(b"reference content")
    return source


def test_failed_copy_retries_without_partial_module_or_backup_collision(tmp_path, monkeypatch):
    paths = get_paths(tmp_path)
    source = legacy_list(paths)
    copy = migrate.shutil.copy2

    def fail_stats(src, dest, **kwargs):
        if Path(src).name == "stats.json":
            raise OSError("simulated disk failure")
        return copy(src, dest, **kwargs)

    monkeypatch.setattr(migrate, "_stamp", lambda: "same-second")
    with monkeypatch.context() as fault:
        fault.setattr(migrate.shutil, "copy2", fail_stats)
        with pytest.raises(OSError, match="disk failure"):
            migrate.migrate(paths)

    assert not paths.module_dir("Book").exists()
    assert Storage(paths).list_names() == []
    assert source.exists()
    assert not list(tmp_path.glob(".lists-migration-*"))
    assert migrate.migrate(paths) == ["Book"]
    assert len(list(tmp_path.glob("lists.backup-same-second-*"))) == 2
    assert Storage(paths).load_stats("Book")["word"]["box"] == 4
    assert (paths.documents_dir("Book") / "reference.pdf").read_bytes() == b"reference content"
    assert not migrate.needs_migration(paths)


def test_retry_preserves_completed_modules_and_new_user_progress(tmp_path, monkeypatch):
    paths = get_paths(tmp_path)
    legacy_list(paths, "A")
    legacy_list(paths, "B")
    copy = migrate.shutil.copy2

    def fail_second(src, dest, **kwargs):
        if Path(src).parent.name == "B" and Path(src).name == "stats.json":
            raise OSError("second module failed")
        return copy(src, dest, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(migrate.shutil, "copy2", fail_second)
        with pytest.raises(OSError, match="second module"):
            migrate.migrate(paths)

    storage = Storage(paths)
    assert storage.list_names() == ["A"]
    words = storage.load_words("A")
    words.add(Word("extra", senses=[Sense("added after migration")]))
    storage.save_words(words)
    storage.save_stats("A", {"word": {"box": 5, "reps": 8}})
    # Normal manifest edits must retain the completed migration record.
    module = storage.load_module("A")
    module.metadata["description"] = "updated"
    storage.save_module(module)

    assert migrate.migrate(paths) == ["B"]
    assert storage.load_words("A").has("extra")
    assert storage.load_stats("A")["word"]["box"] == 5
    assert storage.load_stats("B")["word"]["box"] == 4
    assert not paths.legacy_lists_dir.exists()


@pytest.mark.parametrize("existing", ["empty", "partial", "unrelated"])
def test_existing_unverified_targets_are_kept_and_legacy_remains_pending(tmp_path, existing):
    paths = get_paths(tmp_path)
    source = legacy_list(paths)
    legacy_list(paths, "Other")
    storage = Storage(paths)
    if existing == "unrelated":
        storage.create("Book")
        storage.save_note("Book", "Keep", "personal note")
    else:
        paths.module_dir("Book").mkdir()
        if existing == "partial":
            paths.vocab_dir("Book").mkdir()
            paths.words_file("Book").write_bytes((source / "words.json").read_bytes())
    original = {p.relative_to(paths.module_dir("Book")): p.read_bytes()
                for p in paths.module_dir("Book").rglob("*") if p.is_file()}

    with pytest.raises(RuntimeError, match="existing module.*Book"):
        migrate.migrate(paths)

    assert original == {p.relative_to(paths.module_dir("Book")): p.read_bytes()
                        for p in paths.module_dir("Book").rglob("*") if p.is_file()}
    assert storage.exists("Other")
    assert migrate.needs_migration(paths)
    assert not list(tmp_path.glob("lists.migrated-*"))


@pytest.mark.parametrize("filename, content", [("words.json", "{broken"), ("stats.json", "[]")])
def test_unreadable_json_is_not_published(tmp_path, filename, content):
    paths = get_paths(tmp_path)
    source = legacy_list(paths)
    (source / filename).write_text(content, encoding="utf-8")

    with pytest.raises(ValueError) as info:
        migrate.migrate(paths)

    # The message names the list and its file, not the discarded staging copy.
    message = str(info.value)
    assert "'Book'" in message and filename in message
    assert ".lists-migration-" not in message
    assert not paths.module_dir("Book").exists()
    assert migrate.needs_migration(paths)
    backup = next(tmp_path.glob("lists.backup-*"))
    assert (backup / "Book" / filename).read_text(encoding="utf-8") == content


def test_copy_that_changes_bytes_is_rejected_before_publication(tmp_path, monkeypatch):
    paths = get_paths(tmp_path)
    legacy_list(paths)
    copy = migrate.shutil.copy2

    def corrupt_copy(src, dest, **kwargs):
        result = copy(src, dest, **kwargs)
        if Path(src).name == "stats.json":
            Path(dest).write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(migrate.shutil, "copy2", corrupt_copy)
    with pytest.raises(RuntimeError, match="stats.json changed"):
        migrate.migrate(paths)
    assert not paths.module_dir("Book").exists()
    assert migrate.needs_migration(paths)


def test_failed_retirement_can_retry_without_rewriting_published_module(tmp_path, monkeypatch):
    paths = get_paths(tmp_path)
    legacy_list(paths)
    rename = Path.rename

    def fail_retirement(source, target):
        if source == paths.legacy_lists_dir:
            raise OSError("legacy directory busy")
        return rename(source, target)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "rename", fail_retirement)
        with pytest.raises(OSError, match="directory busy"):
            migrate.migrate(paths)
    manifest = paths.manifest_file("Book").read_bytes()
    assert migrate.migrate(paths) == []
    assert paths.manifest_file("Book").read_bytes() == manifest
    assert not paths.legacy_lists_dir.exists()


def test_legacy_changes_during_copy_prevent_retirement(tmp_path, monkeypatch):
    paths = get_paths(tmp_path)
    source = legacy_list(paths)
    write_manifest = migrate._write_manifest

    def update_legacy(staged, name, fingerprint):
        write_manifest(staged, name, fingerprint)
        (source / "stats.json").write_text('{"word": {"box": 5}}', encoding="utf-8")

    monkeypatch.setattr(migrate, "_write_manifest", update_legacy)
    with pytest.raises(RuntimeError, match="Legacy lists changed"):
        migrate.migrate(paths)
    assert json.loads((source / "stats.json").read_text())["word"]["box"] == 5
    assert not list(tmp_path.glob("lists.migrated-*"))
