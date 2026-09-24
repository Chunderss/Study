import json
import pytest
from vocab.cli.app import App
from vocab.core.models import Sense
from vocab.core.paths import sanitize_module_name
from vocab.study.disambiguate import NlpDisambiguator
from vocab.study.session import StudySession


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_use("Book")
    return app


@pytest.mark.parametrize("name", ["CON.txt", "LPT1", "COM9.md", "book.", "NUL"])
def test_invalid_windows_names(name):
    with pytest.raises(ValueError):
        sanitize_module_name(name)


def test_notes_create_is_exclusive(app):
    app.storage.create_note("Book", "Chapter")
    app.storage.save_note("Book", "Chapter", "keep")
    with pytest.raises(FileExistsError):
        app.storage.create_note("Book", "Chapter.md")
    assert app.storage.load_note("Book", "Chapter") == "keep"


def test_failed_atomic_note_write_cleans_temp_and_keeps_original(app, monkeypatch):
    app.storage.save_note("Book", "Chapter", "keep")
    def fail(*a): raise OSError("disk full")
    monkeypatch.setattr("vocab.core.storage.os.replace", fail)
    with pytest.raises(OSError):
        app.storage.save_note("Book", "Chapter", "new")
    assert app.storage.load_note("Book", "Chapter") == "keep"
    assert list(app.paths.notes_dir("Book").glob("*.tmp")) == []


def test_duplicate_document_does_not_overwrite(app, tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"original")
    app.storage.add_document("Book", path)
    path.write_bytes(b"different")
    with pytest.raises(FileExistsError): app.storage.add_document("Book", path)
    assert (app.paths.documents_dir("Book") / path.name).read_bytes() == b"original"


def test_grade_survives_without_finish_and_finish_cannot_replay(app):
    app.cmd_add("word", manual_def="definition")
    s = StudySession(app.storage, app.scheduler, ["Book"])
    s.answer(True)
    assert app.storage.load_stats("Book")["word"]["box"] == 2
    app.cmd_delete_word("word")
    s.finish()
    assert "word" not in app.storage.load_stats("Book")


def test_reading_callback_cannot_recreate_deleted_module(app):
    app.cmd_delete("Book")
    app.storage.save_reading_pos("Book", "book.pdf", {"page": 1})
    assert not app.paths.module_dir("Book").exists()


def test_file_identity_beats_embedded_json_name(app):
    app.cmd_create("Other")
    path = app.paths.words_file("Book")
    raw = json.loads(path.read_text())
    raw["name"] = "Other"
    path.write_text(json.dumps(raw))
    app.cmd_add("word", manual_def="definition")
    assert app.storage.load_words("Book").has("word")
    assert not app.storage.load_words("Other").has("word")


def test_pos_mismatch_does_not_duplicate_senses(monkeypatch):
    dis = NlpDisambiguator()
    monkeypatch.setattr(dis, "_ensure_tagger", lambda: True)
    monkeypatch.setattr(dis, "_wn_pos_of", lambda *a: "v")
    senses = [Sense("short duration", "noun"), Sense("brief", "adjective")]
    ranked = dis.rank("short", "a short duration", senses)
    assert len(ranked) == len(senses)
    assert all(ranked.count(s) == 1 for s in senses)


def test_empty_lookup_cannot_write_broken_word(app, monkeypatch):
    monkeypatch.setattr(app, "_lookup_senses", lambda *a: ([], "test", ""))
    with pytest.raises(ValueError): app.cmd_add("word")
    assert not app.storage.load_words("Book").words


def test_component_names_are_valid_note_names(app):
    app.storage.create_note("Book", "notes")
    assert "notes" in app.storage.note_names("Book")


def test_case_insensitive_filesystem_cannot_clobber_existing_module(app, monkeypatch):
    from vocab.core.paths import Paths
    from vocab.core.storage import ModuleExists
    class CaseInsensitivePaths(Paths):
        def module_dir(self, name):
            path = super().module_dir(name)
            for existing in self.modules_dir.iterdir():
                if existing.name.casefold() == path.name.casefold():
                    return existing
            return path
    monkeypatch.setattr(app.storage, "paths", CaseInsensitivePaths(app.paths.root))
    app.cmd_add("word", manual_def="keep this definition")
    with pytest.raises(ModuleExists):
        app.cmd_create("book")
    app.cmd_use("book")
    assert app.current_module == "Book"
    assert app.storage.load_words("Book").has("word")
