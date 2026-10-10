"""Note names round-trip: what the app returns reopens the same note."""
import pytest

from vocab.cli.app import App


@pytest.fixture
def storage(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    return app.storage


@pytest.mark.parametrize("typed, stem", [
    ("Space .md", "Space"), ("Double.md.md", "Double"), ("  Trim  ", "Trim"),
    ("Upper.MD", "Upper"), ("Dots.in.name", "Dots.in.name"),
])
def test_created_names_round_trip(storage, typed, stem):
    created = storage.create_note("Book", typed)
    assert created == stem and created in storage.note_names("Book")
    storage.save_note("Book", created, "text")
    assert storage.load_note("Book", created) == "text"
    assert storage.load_note("Book", typed) == "text"  # the typed spelling too
    storage.delete_note("Book", created)
    assert created not in storage.note_names("Book")


def test_a_longer_spelling_never_opens_another_note(storage):
    storage.save_note("Book", "Double", "the short note")
    with pytest.raises(FileExistsError):
        storage.create_note("Book", "Double.md.md")
    assert storage.load_note("Book", "Double") == "the short note"


def test_legacy_files_with_odd_names_stay_reachable(storage):
    notes = storage.paths.notes_dir("Book")
    (notes / "Double.md.md").write_text("legacy double", encoding="utf-8")
    (notes / "Double.md").write_text("plain double", encoding="utf-8")
    listed = storage.note_names("Book")
    assert "Double.md" in listed and "Double" in listed
    assert storage.load_note("Book", "Double.md") == "legacy double"
    assert storage.load_note("Book", "Double") == "plain double"
    # Their recovery copies are separate files.
    storage.save_note_draft("Book", "Double.md", "legacy double", "draft A")
    storage.save_note_draft("Book", "Double", "plain double", "draft B")
    assert storage.load_note_draft("Book", "Double.md")["content"] == "draft A"
    assert storage.load_note_draft("Book", "Double")["content"] == "draft B"
    storage.save_note("Book", "Double.md", "legacy edited")
    assert (notes / "Double.md.md").read_text(encoding="utf-8") == "legacy edited"
    storage.delete_note("Book", "Double.md")
    storage.clear_note_draft("Book", "Double.md")  # as the GUI's discard does next
    assert storage.note_names("Book") == ["Double"]
    assert storage.load_note_draft("Book", "Double")["content"] == "draft B"
