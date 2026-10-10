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


@pytest.mark.parametrize("legacy, listed, plain", [
    ("Double.md.md", "Double.md", "Double"),
    ("Space .md", "Space ", "Space"),  # what the old app saved for "Space .md"
])
def test_legacy_files_with_odd_names_stay_reachable(storage, legacy, listed, plain):
    notes = storage.paths.notes_dir("Book")
    (notes / legacy).write_text("legacy note", encoding="utf-8")
    (notes / f"{plain}.md").write_text("plain note", encoding="utf-8")
    listed_names = storage.note_names("Book")
    assert listed in listed_names and plain in listed_names
    assert storage.load_note("Book", listed) == "legacy note"
    assert storage.load_note("Book", plain) == "plain note"
    # Their recovery copies are separate files.
    storage.save_note_draft("Book", listed, "legacy note", "draft A")
    storage.save_note_draft("Book", plain, "plain note", "draft B")
    assert storage.load_note_draft("Book", listed)["content"] == "draft A"
    assert storage.load_note_draft("Book", plain)["content"] == "draft B"
    storage.save_note("Book", listed, "legacy edited")
    assert (notes / legacy).read_text(encoding="utf-8") == "legacy edited"
    assert (notes / f"{plain}.md").read_text(encoding="utf-8") == "plain note"
    storage.delete_note("Book", listed)
    storage.clear_note_draft("Book", listed)  # as the GUI's discard does next
    assert storage.note_names("Book") == [plain]
    assert storage.load_note_draft("Book", plain)["content"] == "draft B"


def test_a_legacy_name_alone_still_opens(storage):
    notes = storage.paths.notes_dir("Book")
    (notes / "Chapter 1 .md").write_text("legacy", encoding="utf-8")
    assert storage.load_note("Book", "Chapter 1 ") == "legacy"
    storage.delete_note("Book", "Chapter 1 ")
    assert storage.note_names("Book") == []
