"""Startup cleanup owns modules/, but not individual linked subfolders."""
import os

import pytest

from vocab.cli.app import App


TRASH = ".vocab-trash-" + "a" * 32
STAGING = ".vocab-staging-" + "b" * 32


def leftovers(folder):
    for name in (TRASH, STAGING):
        path = folder / name
        path.mkdir()
        (path / "keep.txt").write_bytes(b"outside data")
        old = path.stat().st_mtime - 2 * 24 * 3600
        os.utime(path, (old, old))


def relocate(folder, outside):
    folder.rename(outside)
    try:
        folder.symlink_to(outside, target_is_directory=True)
    except OSError:
        outside.rename(folder)
        pytest.skip("directory symlinks are not available to this user")


@pytest.mark.parametrize("link", ["module", "documents"])
def test_purge_preserves_external_leftovers_behind_individual_links(tmp_path, link):
    app = App(tmp_path / "home")
    app.cmd_create("Book")
    app.cmd_create("Owned")
    folder = (app.paths.module_dir("Book") if link == "module"
              else app.paths.documents_dir("Book"))
    outside = tmp_path / "outside"
    relocate(folder, outside)
    external_documents = outside / "documents" if link == "module" else outside
    leftovers(external_documents)
    owned = app.paths.documents_dir("Owned")
    leftovers(owned)
    leftovers(app.paths.modules_dir)
    App(app.paths.root)
    for name in (TRASH, STAGING):
        assert (external_documents / name / "keep.txt").read_bytes() == b"outside data"
        assert not (owned / name).exists()
        assert not (app.paths.modules_dir / name).exists()


def test_relocated_top_level_modules_still_purges_owned_leftovers(tmp_path):
    app = App(tmp_path / "home")
    app.cmd_create("Book")
    relocate(app.paths.modules_dir, tmp_path / "relocated-modules")
    documents = app.paths.documents_dir("Book")
    leftovers(documents)
    leftovers(app.paths.modules_dir)
    restore_staging = app.paths.modules_dir / ".vs~12345678"
    restore_staging.mkdir()
    old = restore_staging.stat().st_mtime - 2 * 24 * 3600
    os.utime(restore_staging, (old, old))
    App(app.paths.root)
    assert app.storage.exists("Book")
    assert list(documents.iterdir()) == []
    assert sorted(p.name for p in app.paths.modules_dir.iterdir()) == ["Book"]
