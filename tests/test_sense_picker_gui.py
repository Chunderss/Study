"""The desktop sense picker shows and corrects the sense a word studies.

Keys go through the window, as platform input does, and any dialog fails the
test: the picker never asks with a modal.
"""
import json
import threading

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QWidget

from tests import real_nltk
from vocab.cli.app import App
from vocab.core.models import Sense

from .test_gui_reliability import qt, window  # noqa: F401
from .test_native_hotkeys import focus, leader

VERB_USE = "His energy began to flag."
THREE = [Sense("a rectangular emblem of cloth", "noun"),
         Sense("become less intense", "verb", "his energy flagged"),
         Sense("decorate with flags", "verb")]


@pytest.fixture
def no_dialogs(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"a modal dialog was opened: {args[1:3]}")
    for name in ("question", "information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, refuse)


def settle(window):
    window.ctx._pool.waitForDone(5000)
    for _ in range(300):
        if not window.ctx.busy:
            break
        QTest.qWait(10)
    QTest.qWait(10)


def raw(window, word, module="Book"):
    return json.loads(window.app.paths.words_file(module).read_text("utf-8"))["words"].get(word)


def definitions(window, word):
    return [s["definition"] for s in raw(window, word)["senses"]]


def rows(picker):
    return [picker.list.item(i).text() for i in range(picker.list.count())]


def mock_lookup(window, monkeypatch, senses=THREE):
    monkeypatch.setattr(window.app, "_lookup_senses",
                        lambda word, sentence="": (list(senses), "wordnet", ""))


def reader_add(window, qt, monkeypatch, word="flag", senses=THREE):
    """Add a word as a reader does, while the user types in the Vocab pane."""
    mock_lookup(window, monkeypatch, senses)
    vocab = window.workspace.focused.component
    native = focus(window, vocab.add_input, qt)
    window._add_highlighted_word(word, VERB_USE, "Book")
    settle(window)
    return vocab, native


def test_reader_add_shows_the_picker_without_taking_focus(window, qt, monkeypatch, no_dialogs):
    def sizes():
        qt.processEvents()
        return window.workspace.height(), window.workspace.width(), window.height()
    before = sizes()
    vocab, _native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    assert picker.isVisible()
    assert picker.title.text() == "flag" and picker.where.text() == "Book · wordnet"
    assert picker.state.text() == "Chosen automatically"
    assert picker.context.text() == f"“{VERB_USE}”"
    assert rows(picker) == ["noun · a rectangular emblem of cloth   (studied)",
                            "verb · become less intense", "verb · decorate with flags"]
    assert picker.list.item(1).toolTip() == "“his energy flagged”"
    assert qt.focusWidget() is vocab.add_input  # reading or typing goes on
    assert sizes() == before  # an overlay: no pane moves
    area = QRect(window.workspace.mapTo(window, QPoint(0, 0)), window.workspace.size())
    assert area.contains(picker.geometry())
    assert "Ctrl+B w lists them all." in window.message.text()


def test_leader_w_and_keys_choose_a_sense_and_keep_progress(window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    stats = window.app.storage.load_stats("Book")
    stats["flag"]["box"] = 3
    window.app.storage.save_stats("Book", stats)
    progress = window.app.paths.stats_file("Book").read_bytes()
    leader(native, Qt.Key_W)
    picker = window.sense_picker
    assert qt.focusWidget() is picker.list and picker.list.currentRow() == 0  # the studied one
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Return)  # study only this sense
    assert definitions(window, "flag") == [
        "become less intense", "a rectangular emblem of cloth", "decorate with flags"]
    assert raw(window, "flag")["choice"] == "user"
    assert window.app.paths.stats_file("Book").read_bytes() == progress
    assert rows(picker)[0] == "verb · become less intense   (studied)"
    assert picker.state.text() == "Chosen by you"
    assert picker.status.text() == ("'flag' in 'Book' now studies: verb: become less intense"
                                    "  (its progress is kept)")
    assert "become less intense" in vocab.words.toPlainText()
    assert picker.list.currentRow() == -1  # a second Enter does nothing
    QTest.keyClick(native, Qt.Key_Escape)
    assert not picker.isVisible() and qt.focusWidget() is vocab.add_input


def test_leader_w_again_closes_and_reopens_the_last_word(window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    leader(native, Qt.Key_W)
    leader(native, Qt.Key_W)
    assert not picker.isVisible() and qt.focusWidget() is vocab.add_input
    leader(native, Qt.Key_W)
    assert picker.isVisible() and qt.focusWidget() is picker.list
    assert picker.title.text() == "flag"


def test_esc_closes_a_picker_shown_after_a_lookup_from_where_you_type(
        window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    QTest.keyClick(native, Qt.Key_Escape)  # focus never moved into it
    assert not picker.isVisible() and qt.focusWidget() is vocab.add_input
    QTest.keyClicks(vocab.add_input, "ab")
    QTest.keyClick(native, Qt.Key_Escape)  # hidden: Esc is the pane's again
    assert vocab.add_input.text() == "ab"


def test_enter_needs_a_highlighted_sense(window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    before = window.app.paths.words_file("Book").read_bytes()
    picker.list.setFocus()
    picker.list.setCurrentRow(-1)
    QTest.keyClick(picker.list, Qt.Key_Return)
    assert window.app.paths.words_file("Book").read_bytes() == before
    assert picker.status.text() == "Highlight a sense with the arrow keys, then press Enter."


def test_picker_refuses_a_choice_on_a_word_changed_elsewhere(
        window, qt, monkeypatch, no_dialogs, tmp_path):
    vocab, native = reader_add(window, qt, monkeypatch)
    leader(native, Qt.Key_W)
    QTest.keyClick(native, Qt.Key_Down)
    App(tmp_path).cmd_add("flag", target="Book", manual_def="my own meaning")  # another process
    QTest.keyClick(native, Qt.Key_Return)
    picker = window.sense_picker
    assert raw(window, "flag")["dictionary"] == "manual"
    assert picker.status.text().startswith("! 'flag' changed since its senses were listed")
    assert rows(picker) == ["my own meaning   (studied)"]
    assert not picker.list.item(0).flags() & Qt.ItemIsSelectable
    assert picker.state.text() == "Your own definition" and not picker.use_btn.isEnabled()


def test_no_row_is_highlighted_after_a_change_elsewhere(window, qt, monkeypatch, no_dialogs, tmp_path):
    vocab, native = reader_add(window, qt, monkeypatch)
    leader(native, Qt.Key_W)
    QTest.keyClick(native, Qt.Key_Down)
    other = App(tmp_path)
    other.cmd_use("Book")
    other.cmd_sense("3", "flag")  # another program reorders the senses
    window.ctx.changed.emit()     # any later change in this window reloads it
    picker = window.sense_picker
    assert rows(picker)[0] == "verb · decorate with flags   (studied)"
    assert picker.list.currentRow() == -1
    assert picker.status.text() == "'flag' changed elsewhere; this is its current list."
    before = window.app.paths.words_file("Book").read_bytes()
    QTest.keyClick(native, Qt.Key_Return)
    assert window.app.paths.words_file("Book").read_bytes() == before


def test_a_reader_lookup_started_before_a_choice_keeps_the_choice(
        window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    release = threading.Event()

    def slow(word, sentence=""):
        assert release.wait(5)
        return list(THREE), "wordnet", ""
    monkeypatch.setattr(window.app, "_lookup_senses", slow)
    window._add_highlighted_word("flag", "They raised the flag.", "Book")  # in flight
    leader(native, Qt.Key_W)
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Return)
    release.set()
    settle(window)
    assert definitions(window, "flag")[0] == "decorate with flags"
    assert raw(window, "flag")["context"] == VERB_USE
    assert "already in 'Book'" in window.message.text()


def test_a_focused_picker_keeps_its_word_when_a_lookup_finishes(
        window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    leader(native, Qt.Key_W)
    QTest.keyClick(native, Qt.Key_Down)
    picker = window.sense_picker
    other = QWidget()  # the user switches to another program meanwhile
    other.show()
    other.activateWindow()
    qt.processEvents()
    window._add_highlighted_word("bank", "The river bank.", "Book")
    settle(window)
    assert picker.title.text() == "flag" and picker.list.currentRow() == 1
    other.close()
    focus(window, picker.list, qt)
    QTest.keyClick(native, Qt.Key_Return)  # still the sense the user highlighted
    assert definitions(window, "flag")[0] == "become less intense"
    assert definitions(window, "bank")[0] == "a rectangular emblem of cloth"
    leader(native, Qt.Key_W)  # closes; the next one opens the newer word
    leader(native, Qt.Key_W)
    assert picker.title.text() == "bank"


def test_an_untouched_picker_follows_the_latest_lookup(window, qt, monkeypatch, no_dialogs):
    reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    window._add_highlighted_word("bank", "The river bank.", "Book")
    settle(window)
    assert picker.isVisible() and picker.title.text() == "bank"
    mock_lookup(window, monkeypatch, THREE[:1])
    window._add_highlighted_word("ice", "Thin ice.", "Book")  # one sense: nothing to choose
    settle(window)
    assert not picker.isVisible()
    window.ctx.add_word("own", manual="my own", target="Book")
    assert not picker.isVisible()


def test_all_senses_come_from_real_wordnet_in_the_background(window, qt, monkeypatch, no_dialogs):
    real_nltk.require("corpora/wordnet.zip")
    window.app.config.active_dictionary = "wordnet"
    vocab = window.workspace.focused.component
    native = focus(window, vocab.add_input, qt)
    QTest.keyClicks(vocab.add_input, "flag")
    QTest.keyClick(native, Qt.Key_Return)  # typed: WordNet's first six senses, all nouns
    settle(window)
    picker = window.sense_picker
    assert len(rows(picker)) == 6
    QTest.mouseClick(picker.more_btn, Qt.LeftButton)
    assert window.ctx.busy and not picker.more_btn.isEnabled()  # on the lookup thread
    settle(window)
    texts = rows(picker)
    assert texts[6] == "More from wordnet — saved only if you choose one"
    assert len(texts) == 13 and texts[12] == "verb · become less intense"
    assert picker.status.text() == "6 more from wordnet, saved only if you choose one."
    assert len(definitions(window, "flag")) == 6  # nothing saved yet
    leader(native, Qt.Key_W)
    for _ in range(12):
        QTest.keyClick(native, Qt.Key_Down)  # the separator is skipped
    assert picker.list.currentRow() == 12
    QTest.keyClick(native, Qt.Key_Return)
    word = window.app.storage.load_words("Book").words["flag"]
    assert word.primary_definition() == "become less intense" and len(word.senses) == 7


def test_an_all_senses_result_for_another_word_is_dropped(window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    window.app.cmd_add("bank", target="Book")
    release = threading.Event()
    real_more = window.app.more_senses

    def slow(listing):
        assert release.wait(5)
        return real_more(listing).with_extra([Sense("flag's own extra sense", "verb")])
    monkeypatch.setattr(window.app, "more_senses", slow)
    picker = window.sense_picker
    picker.find_more()
    assert picker.show_word("Book", "bank", focus=True)  # e.g. clicked in the Vocab list
    before = window.app.paths.words_file("Book").read_bytes()
    release.set()
    settle(window)
    assert picker.title.text() == "bank" and not any("extra" in text for text in rows(picker))
    assert picker.more_btn.isEnabled()
    QTest.keyClick(picker.list, Qt.Key_Return)  # keeps bank's studied sense
    assert all("extra" not in d for d in definitions(window, "bank"))
    assert definitions(window, "flag") == [s.definition for s in THREE]
    assert json.loads(before)["words"]["bank"]["senses"] == raw(window, "bank")["senses"]


def test_a_manual_definition_offers_nothing_to_choose(window, qt, monkeypatch, no_dialogs):
    window.app.cmd_add("ephemeral", target="Book", manual_def="my careful definition")
    before = window.app.paths.words_file("Book").read_bytes()
    picker = window.sense_picker
    picker.show_word("Book", "ephemeral", focus=True)
    assert picker.state.text() == "Your own definition"
    assert picker.status.text() == "Your own definition: there are no dictionary senses to choose."
    assert not picker.use_btn.isEnabled() and not picker.more_btn.isEnabled()
    QTest.keyClick(picker.list, Qt.Key_Return)
    assert window.app.paths.words_file("Book").read_bytes() == before


def test_a_deleted_module_or_damaged_file_disables_the_picker(
        window, qt, monkeypatch, no_dialogs, tmp_path):
    vocab, native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    path = window.app.paths.words_file("Book")
    path.write_text("{damaged", encoding="utf-8")
    window.ctx.changed.emit()
    assert picker.status.text().startswith("! Could not read") and rows(picker) == []
    assert not picker.use_btn.isEnabled()
    leader(native, Qt.Key_W)
    QTest.keyClick(native, Qt.Key_Return)
    assert path.read_text(encoding="utf-8") == "{damaged"
    App(tmp_path).cmd_delete("Book")
    window.ctx.changed.emit()
    assert picker.status.text() == "! Module 'Book' is no longer available."


def test_leader_w_without_a_word_says_what_to_do(window, qt, no_dialogs):
    native = focus(window, window.workspace.focused.component.add_input, qt)
    leader(native, Qt.Key_W)
    assert not window.sense_picker.isVisible()
    assert window.message.text() == "! Add a word first, or click a word in Vocab to see its senses."
    assert any(row[0] == "w" and row[2].startswith("Senses of the last added word")
               for row in window._keymap())


def test_the_picker_keeps_clear_of_the_help_panel_and_the_focused_pane(
        window, qt, monkeypatch, no_dialogs):
    window._toggle_help()
    vocab, native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    assert picker.geometry().right() < window._help.geometry().left()
    window._toggle_help()
    picker.hide()
    window.workspace.split_vertical()  # the focused Vocab pane is now on the right
    qt.processEvents()
    QTest.qWait(20)
    right = window.workspace.focused
    focus(window, right.component.add_input, qt)
    window._add_highlighted_word("bank", "The river bank.", "Book")
    settle(window)
    assert picker.isVisible()
    pane = QRect(right.mapTo(window, QPoint(0, 0)), right.size())
    assert not picker.geometry().intersects(pane)


# ---- from the Vocab list -----------------------------------------------------
def two_words(window, monkeypatch):
    mock_lookup(window, monkeypatch)
    window.app.cmd_add("flag", target="Book")
    window.app.cmd_add("bank", target="Book", manual_def="mine")
    window.ctx.changed.emit()
    return window.workspace.focused.component


def test_a_word_in_the_vocab_list_opens_its_senses_from_the_keyboard(
        window, qt, monkeypatch, no_dialogs):
    vocab = two_words(window, monkeypatch)
    native = focus(window, vocab.words, qt)
    QTest.keyClick(native, Qt.Key_Tab)    # first word link: bank
    QTest.keyClick(native, Qt.Key_Tab)    # second: flag
    QTest.keyClick(native, Qt.Key_Return)
    picker = window.sense_picker
    assert picker.isVisible() and qt.focusWidget() is picker.list
    assert picker.title.text() == "flag" and picker.state.text() == "Chosen automatically"
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Return)
    assert definitions(window, "flag")[0] == "become less intense"
    QTest.keyClick(native, Qt.Key_Escape)
    assert qt.focusWidget() is vocab.words  # back in the list


def test_a_click_on_a_word_opens_its_senses_even_your_own(window, qt, monkeypatch, no_dialogs):
    vocab = two_words(window, monkeypatch)
    focus(window, vocab.add_input, qt)
    document = vocab.words.document()
    cursor = document.find("bank")
    rect = vocab.words.cursorRect(cursor)
    QTest.mouseClick(vocab.words.viewport(), Qt.LeftButton, pos=rect.center() - QPoint(8, 0))
    picker = window.sense_picker
    assert picker.isVisible() and picker.title.text() == "bank"
    assert picker.state.text() == "Your own definition"


def test_the_vocab_list_keeps_its_place_and_shows_words_as_typed(window, qt, monkeypatch):
    for i in range(80):
        window.app.cmd_add(f"word{i:02d}", target="Book", manual_def=f"meaning {i}")
    window.app.cmd_add("<b>x</b> & y", target="Book", manual_def="<i>not markup</i>")
    window.ctx.changed.emit()
    vocab = window.workspace.focused.component
    qt.processEvents()
    scroll = vocab.words.verticalScrollBar()
    assert scroll.maximum() > 0
    scroll.setValue(scroll.maximum() // 2)
    middle = scroll.value()
    window.ctx.changed.emit()  # e.g. a word added in a reader
    assert scroll.value() == middle
    text = vocab.words.toPlainText()
    assert "<b>x</b> & y" in text and "<i>not markup</i>" in text
    window.app.cmd_create("Other")
    window.app.cmd_add("w", target="Other", manual_def="m")
    for _ in range(40):
        window.app.cmd_add(f"other{_:02d}", target="Other", manual_def="m")
    window.app.cmd_use("Other")
    window.ctx.module_changed.emit("Other")
    assert scroll.value() == 0  # another module starts at the top


def test_the_picker_starts_the_reviews_over_only_when_asked(window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    stats = window.app.storage.load_stats("Book")
    stats["flag"]["box"] = 3
    window.app.storage.save_stats("Book", stats)
    picker = window.sense_picker
    leader(native, Qt.Key_W)
    QTest.keyClick(native, Qt.Key_Tab)    # to "Start its reviews over"
    assert qt.focusWidget() is picker.restart and not picker.restart.isChecked()
    QTest.keyClick(native, Qt.Key_Space)
    assert picker.restart.isChecked()
    QTest.keyClick(native, Qt.Key_Tab, Qt.ShiftModifier)
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Return)
    assert definitions(window, "flag")[0] == "become less intense"
    assert window.app.storage.load_stats("Book")["flag"] == window.app.scheduler.new_card()
    assert picker.status.text().endswith("(its reviews start over, due now)")
    assert not picker.restart.isChecked()  # asked again for the next choice
    stats = window.app.storage.load_stats("Book")
    stats["flag"]["box"] = 2
    window.app.storage.save_stats("Book", stats)
    picker.list.setFocus()
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Return)
    assert window.app.storage.load_stats("Book")["flag"]["box"] == 2


# ---- several senses studied ------------------------------------------------
def test_space_checks_several_and_the_button_keeps_them(window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    assert [picker.list.item(i).checkState() for i in range(3)] == [
        Qt.Checked, Qt.Unchecked, Qt.Unchecked]
    leader(native, Qt.Key_W)
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Space)
    assert picker.list.item(2).checkState() == Qt.Checked
    QTest.mouseClick(picker.use_btn, Qt.LeftButton)  # Study checked
    entry = raw(window, "flag")
    assert entry["kept"] == 2
    assert definitions(window, "flag") == [
        "a rectangular emblem of cloth", "decorate with flags", "become less intense"]
    assert rows(picker)[:2] == ["noun · a rectangular emblem of cloth   (studied)",
                                "verb · decorate with flags   (studied)"]
    text = vocab.words.toPlainText()
    assert "[noun] a rectangular emblem of cloth" in text and "[verb] decorate with flags" in text
    picker.list.setFocus()
    picker.list.setCurrentRow(2)
    QTest.keyClick(picker.list, Qt.Key_Return)  # Enter: only the highlighted sense
    assert "kept" not in raw(window, "flag")
    assert definitions(window, "flag")[0] == "become less intense"


def click_word(vocab, word):
    rect = vocab.words.cursorRect(vocab.words.document().find(word))
    QTest.mouseClick(vocab.words.viewport(), Qt.LeftButton, pos=rect.center() - QPoint(8, 0))


def test_opening_then_enter_keeps_every_studied_sense(window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    QTest.keyClick(native, Qt.Key_Escape)
    window.app.cmd_sense("3,1", "flag")
    kept = window.app.paths.words_file("Book").read_bytes()
    leader(native, Qt.Key_W)  # opens with the first studied sense highlighted
    assert qt.focusWidget() is picker.list and picker.list.currentRow() == 0
    QTest.keyClick(native, Qt.Key_Return)  # to confirm or close: nothing is dropped
    assert window.app.paths.words_file("Book").read_bytes() == kept
    assert picker.status.text() == ("'flag' in 'Book' already studies 2 senses: verb: decorate "
                                    "with flags; noun: a rectangular emblem of cloth  "
                                    "(nothing was changed)")
    QTest.keyClick(native, Qt.Key_Escape)
    window.ctx.changed.emit()
    click_word(vocab, "flag")  # in the Vocab list
    assert qt.focusWidget() is picker.list and picker.list.currentRow() == 0
    QTest.keyClick(native, Qt.Key_Return)
    assert window.app.paths.words_file("Book").read_bytes() == kept
    QTest.keyClick(native, Qt.Key_Down)    # a highlight the user moved is the user's pick,
    QTest.keyClick(native, Qt.Key_Up)      # even back on the first row
    QTest.keyClick(native, Qt.Key_Return)  # Enter: only this sense
    assert "kept" not in raw(window, "flag")
    assert definitions(window, "flag")[0] == "decorate with flags"


def test_opening_after_study_checked_then_enter_keeps_them(window, qt, monkeypatch, no_dialogs):
    vocab, native = reader_add(window, qt, monkeypatch)
    picker = window.sense_picker
    leader(native, Qt.Key_W)
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Space)
    QTest.mouseClick(picker.use_btn, Qt.LeftButton)  # Study checked: two senses
    assert raw(window, "flag")["kept"] == 2
    kept = window.app.paths.words_file("Book").read_bytes()
    picker.list.setFocus()
    QTest.keyClick(native, Qt.Key_Escape)
    leader(native, Qt.Key_W)  # reopens the last word
    QTest.keyClick(native, Qt.Key_Return)
    assert window.app.paths.words_file("Book").read_bytes() == kept


def test_study_view_shows_and_grades_every_kept_sense(window, qt, monkeypatch):
    from vocab.gui.study_view import StudyView
    from vocab.study.judge import KeywordJudge
    from vocab.study.session import StudySession
    mock_lookup(window, monkeypatch)
    window.app.cmd_add("flag", target="Book")
    window.app.cmd_sense("3,2", "flag")
    view = StudyView(StudySession(window.app.storage, window.app.scheduler, ["Book"]))
    view.show()
    view.setFocus()
    qt.processEvents()
    QTest.keyClick(view, Qt.Key_Space)
    assert view.answer.text() == ("1. verb:  decorate with flags\n\n"
                                  "2. verb:  become less intense\n“his energy flagged”")
    view.deleteLater()
    session = StudySession(window.app.storage, window.app.scheduler, ["Book"],
                           mechanical=True, judge=KeywordJudge())
    view = StudyView(session)
    view.show()
    qt.processEvents()
    view.input.setText("grow less intense")
    view._submit_typed()
    assert view.answer.text() == "reference:  decorate with flags; become less intense"
    assert window.app.storage.load_stats("Book")["flag"]["box"] == 1  # not recorded yet
    QTest.keyClick(view, Qt.Key_Return)  # keep the judge's verdict
    assert window.app.storage.load_stats("Book")["flag"]["box"] == 2
    view.deleteLater()
    qt.processEvents()
