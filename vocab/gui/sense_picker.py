"""The sense picker: which sense a word studies, and the others to choose from.

A small panel over the workspace, like the help panel: it never opens a
dialog, never resizes a pane, and appears after a lookup without taking
keyboard focus. Ctrl+B w moves into it with the first studied sense
highlighted, where Enter keeps every sense the word studies; once the user
moves the highlight, Enter studies only the highlighted sense. Space checks
several for Study checked, and Esc closes it, returning focus to where it was.

Every change goes through App.choose_senses, the path SENSE uses in the
terminal, so a choice made from senses that changed since they were shown
(another window or program, a newer lookup) is refused rather than applied
to the wrong sense.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetrics, QTextLayout, QTextOption
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from shiboken6 import isValid

from . import theme

PANEL_WIDTH = 460
MARGIN = 8
BOTTOM_MARGIN = 44   # keeps a bottom-corner panel above a pane's input row
LIST_ROWS = 5        # sense rows the list keeps however long the texts above and below
CONTEXT_LINES = 3    # the sentence, then '…'
STATUS_LINES = 2     # the reply, then '…' (the message line and the Console have it all)


def clip_lines(text: str, font, width: int, lines: int) -> str:
    """``text`` as a word-wrapped label ``width`` pixels wide shows it, cut to
    ``lines`` lines with '…' ending the last when it needs more."""
    if not text or width <= 0:
        return text
    option = QTextOption()
    option.setWrapMode(QTextOption.WordWrap)  # as a label wraps
    layout = QTextLayout(text.replace("\n", "\u2028"), font)  # a label's line breaks
    layout.setTextOption(option)
    starts = []
    layout.beginLayout()
    while len(starts) <= lines:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        starts.append(line.textStart())
    layout.endLayout()
    if len(starts) <= lines:
        return text
    start = starts[lines - 1]
    last = text[start:starts[lines]].rstrip() + "…"
    return text[:start] + QFontMetrics(font).elidedText(last, Qt.ElideRight, width)


class _ClippedLabel(QLabel):
    """A wrapped plain-text label of at most ``lines`` lines, so a long text
    cannot take the sense list's room: the rest is cut with '…', and the
    tooltip and accessible name have it in full. text() is the full text."""

    def __init__(self, lines: int, object_name: str = ""):
        super().__init__("")
        if object_name:
            self.setObjectName(object_name)
        self._lines, self._full = lines, ""
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)

    def text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:
        self._full = text
        self._fit()

    def _shown(self, width: int) -> str:
        return clip_lines(self._full, self.font(), width, self._lines)

    def _fit(self) -> None:
        shown = self._shown(self.contentsRect().width())
        super().setText(shown)
        whole = self._full if shown != self._full else ""
        self.setToolTip(whole)
        self.setAccessibleName(whole)  # empty: the text shown

    def heightForWidth(self, width: int) -> int:
        margins = self.contentsMargins()
        inner = width - margins.left() - margins.right()
        if not self._full or inner <= 0:
            return super().heightForWidth(width)
        box = self.fontMetrics().boundingRect(QRect(0, 0, inner, 1 << 20),
                                              Qt.AlignLeft | Qt.TextWordWrap,
                                              self._shown(inner))
        return box.height() + margins.top() + margins.bottom()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._fit()


class _SenseList(QListWidget):
    """Enter studies the highlighted sense; Esc closes the picker."""

    def __init__(self, picker):
        super().__init__()
        self._picker = picker

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._picker.use_current()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self._picker.hide_and_return()
            event.accept()
            return
        super().keyPressEvent(event)


class SensePicker(QFrame):
    def __init__(self, ctx, parent):
        super().__init__(parent)
        self.ctx, self.app = ctx, ctx.app
        self.setObjectName("SensePicker")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(
            f"#SensePicker {{ background: {theme.BG_PANEL}; border: 2px solid {theme.WALNUT};"
            " border-radius: 6px; }")
        self._target = None       # (module, word) shown, even when it cannot be read
        self._listing = None      # its SenseListing, or None
        self._more_token = None   # the All senses lookup still wanted
        self._return_to = None    # where Esc puts keyboard focus back
        self._opened_row = None   # the row focus_list highlighted, until the user moves
        self._corner = 0          # the corner place() chose, kept while the content changes

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)
        head = QHBoxLayout()
        self.title = QLabel("", objectName="Title")
        self.title.setTextFormat(Qt.PlainText)
        head.addWidget(self.title, 1)
        self.where = QLabel("", objectName="Muted")
        self.where.setTextFormat(Qt.PlainText)
        head.addWidget(self.where)
        root.addLayout(head)
        self.state = QLabel("")
        self.state.setTextFormat(Qt.PlainText)
        root.addWidget(self.state)
        self.context = _ClippedLabel(CONTEXT_LINES, "Muted")
        root.addWidget(self.context)
        self.list = _SenseList(self)
        self.list.itemDoubleClicked.connect(lambda _item: self.use_current())
        # A highlight the user moved or clicked is the user's pick.
        self.list.currentRowChanged.connect(self._forget_opened_row)
        self.list.itemPressed.connect(self._forget_opened_row)
        root.addWidget(self.list, 1)
        self.status = _ClippedLabel(STATUS_LINES)
        root.addWidget(self.status)
        self.restart = QCheckBox("Start its reviews over")
        self.restart.setToolTip("Unchecked, the word keeps its progress when you choose a sense.")
        root.addWidget(self.restart)
        buttons = QHBoxLayout()
        self.use_btn = QPushButton("Study checked")
        self.use_btn.clicked.connect(self.use_checked)
        self.more_btn = QPushButton("All senses")
        self.more_btn.clicked.connect(self.find_more)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.hide_and_return)
        for button in (self.use_btn, self.more_btn, self.close_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        root.addLayout(buttons)
        self.hint = QLabel("Enter: study only the sense you highlight · Space: check several · "
                           "Esc: close", objectName="Muted")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        for label in (self.title, self.where, self.state, self.context, self.status, self.hint):
            label.setFocusPolicy(Qt.NoFocus)
        self.hide()
        ctx.changed.connect(self.refresh)
        ctx.more_senses_done.connect(self._more_done)

    # ---- state ------------------------------------------------------------
    @property
    def listing(self):
        return self._listing

    def has_focus(self) -> bool:
        """Whether keyboard focus is in the picker, even while another window
        is active (QApplication.focusWidget() is then None)."""
        widget = self.window().focusWidget()
        return widget is not None and self.isAncestorOf(widget)

    def show_word(self, module: str, word: str, focus: bool = False) -> bool:
        """Show a word's senses. A picker the user is working in is never
        switched to another word; returns whether this word is shown."""
        key = (module, word.strip().lower())
        shown = self._target and (self._target[0], self._target[1].strip().lower())
        if self.isVisible() and self.has_focus() and shown != key:
            return False
        self._target = (module, word)
        self._load()
        self.place()
        self.show()
        self.raise_()
        if focus:
            self.focus_list()
        return True

    def offer(self, module: str, word: str) -> bool:
        """After a lookup: show a word with senses to choose from, without
        taking focus. A picker the user is working in stays as it is; an
        untouched one that has nothing to offer for this word closes."""
        if self.isVisible() and self.has_focus():
            return False
        try:
            listing = self.app.sense_listing(module, word)
        except Exception:
            listing = None
        if listing is None or listing.manual or len(listing.offered) < 2:
            self.hide()
            return False
        return self.show_word(module, word)

    def _load(self, status: str = "") -> None:
        """Read the shown word again. Never raises: problems are shown, and
        the controls are disabled, so Enter cannot act on a stale list."""
        self._more_token = None
        module, word = self._target
        try:
            listing = self.app.sense_listing(module, word)
        except Exception as error:
            if not self.app.storage.exists(module):
                error = f"Module '{module}' is no longer available."
            self._listing = None
            self._fill(None, status=f"! {error}")
            return
        self._listing = listing
        self._fill(listing, status=status)

    def _fill(self, listing, status: str = "") -> None:
        module, word = self._target
        self.title.setText(word.strip())
        self.list.clear()
        self.list.setAccessibleName(f"Senses of {word.strip()}")
        if listing is None:
            self.where.setText(module)
            self.state.setText("")
            self.context.setText("")
        else:
            self.title.setText(listing.word)
            self.where.setText(f"{listing.module} · {listing.dictionary or '?'}")
            self.state.setText("Your own definition" if listing.manual
                               else "Chosen by you" if listing.chosen
                               else "Chosen automatically")
            self.context.setText(f"“{listing.context}”" if listing.context
                                 else "Added without a sentence")
            for index, sense in enumerate(listing.senses):
                self._add_row(sense, index, studied=index < listing.kept,
                              choosable=not listing.manual)
            if listing.extra:
                separator = QListWidgetItem(f"More from {listing.dictionary} — saved only if "
                                            "you choose one")
                separator.setFlags(Qt.NoItemFlags)
                self.list.addItem(separator)
                for index, sense in enumerate(listing.extra, len(listing.senses)):
                    self._add_row(sense, index, studied=False, choosable=True)
            if listing.manual and not status:
                status = "Your own definition: there are no dictionary senses to choose."
            elif listing.note and not status:
                status = listing.note
        self.list.setCurrentRow(-1)  # Enter acts only on a row the user picked
        self.restart.setChecked(False)  # asked for each choice, never carried over
        self.list.setAccessibleDescription(self.state.text())
        usable = listing is not None and not listing.manual
        self.use_btn.setEnabled(usable)
        self.restart.setEnabled(usable)
        self.more_btn.setEnabled(usable and not listing.extra)
        self._say(status)

    def _add_row(self, sense, index, studied, choosable):
        text = f"{sense.pos} · {sense.definition}" if sense.pos else sense.definition
        item = QListWidgetItem(text + ("   (studied)" if studied else ""))
        item.setData(Qt.UserRole, index)
        if sense.example:
            item.setToolTip(f"“{sense.example}”")
        if studied:
            font = QFont(item.font())
            font.setBold(True)
            item.setFont(font)
        if choosable:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if studied else Qt.Unchecked)
        else:
            item.setFlags(Qt.ItemIsEnabled)
        self.list.addItem(item)

    def refresh(self) -> None:
        """Data changed somewhere: reload only if this word changed. Reads
        without the write lock unless it did, and never raises."""
        if not self.isVisible() or self._target is None:
            return
        try:
            if (self._listing is not None
                    and self.app.peek_word_state(*self._target) == self._listing.expected):
                return
        except Exception:
            pass
        self._load(status=f"'{self._target[1].strip()}' changed elsewhere; this is its "
                          "current list.")

    def _say(self, status: str) -> None:
        self.status.setText(status)
        self.fit_contents()

    # ---- actions ----------------------------------------------------------
    def _forget_opened_row(self, *_args) -> None:
        self._opened_row = None

    def use_current(self) -> None:
        """Enter: study only the highlighted sense. On the row the picker
        highlighted when it opened, before the user moved, keep every sense
        the word studies instead, so opening and pressing Enter never drops
        one of several (with one, it confirms the sense as chosen by you)."""
        listing = self._listing
        if listing is None or listing.manual:
            return
        item = self.list.currentItem()
        if item is None or not item.flags() & Qt.ItemIsSelectable:
            self._say("Highlight a sense with the arrow keys, then press Enter.")
            return
        if self._opened_row is not None and self.list.currentRow() == self._opened_row:
            self._choose(list(range(listing.kept)))
            return
        self._choose([item.data(Qt.UserRole)])

    def use_checked(self) -> None:
        """Study the checked senses, in list order (the first comes first)."""
        listing = self._listing
        if listing is None or listing.manual:
            return
        checked = [item.data(Qt.UserRole) for item in
                   (self.list.item(row) for row in range(self.list.count()))
                   if item.flags() & Qt.ItemIsUserCheckable and item.checkState() == Qt.Checked]
        if not checked:
            self._say("Check at least one sense (Space), then Study checked.")
            return
        self._choose(checked)

    def _choose(self, chosen) -> None:
        try:
            reply, listing = self.app.choose_senses(self._listing, chosen,
                                                    restart=self.restart.isChecked())
        except Exception as error:
            self.ctx.log.emit(f"! {error}")
            self._load(status=f"! {error}")
            return
        self._listing = listing
        self._fill(listing, status=reply)
        self.ctx.log.emit(reply)
        self.ctx.changed.emit()

    def find_more(self) -> None:
        """List the dictionary's other senses, looked up in the background."""
        listing = self._listing
        if listing is None or listing.manual or self._more_token is not None:
            return
        self._more_token = self.ctx.find_more_senses(listing)
        self.more_btn.setEnabled(False)
        self._say(f"Looking up the other senses in {listing.dictionary}…")

    def _more_done(self, token, result) -> None:
        if token != self._more_token:
            return  # asked for a word no longer shown
        self._more_token = None
        current = self._listing
        if current is None or (result.module, result.key, result.expected) != (
                current.module, current.key, current.expected):
            return  # the word changed meanwhile; its list was reloaded
        listing = current.with_extra(result.extra, result.note)
        row, restart, opened = self.list.currentRow(), self.restart.isChecked(), self._opened_row
        checks = [self.list.item(i).checkState() for i in range(self.list.count())]
        self._listing = listing
        count = len(listing.extra)
        self._fill(listing, status=listing.note or (
            f"{count} more from {listing.dictionary}, saved only if you choose one."))
        # The same word: keep the user's place, checks and option.
        for i, state in enumerate(checks):
            if self.list.item(i).flags() & Qt.ItemIsUserCheckable:
                self.list.item(i).setCheckState(state)
        self.list.setCurrentRow(row)
        self._opened_row = opened
        self.restart.setChecked(restart)

    # ---- focus and placement ---------------------------------------------
    def focus_list(self) -> None:
        focus = self.window().focusWidget()
        if focus is not None and not self.isAncestorOf(focus):
            self._return_to = focus
        self.raise_()
        if self.list.currentRow() < 0 and self._listing is not None and not self._listing.manual:
            # The first studied sense, for the arrow keys to start from. Enter
            # there keeps every studied sense, not only this one (use_current).
            self.list.setCurrentRow(0)
            self._opened_row = 0
        self.list.setFocus(Qt.ShortcutFocusReason)

    def hide_and_return(self) -> None:
        had_focus = self.has_focus()
        self.hide()
        if not had_focus:
            return
        target = self._return_to
        if target is not None and isValid(target) and target.isVisible():
            target.setFocus(Qt.OtherFocusReason)
            return
        pane = getattr(self.window(), "workspace", None)
        pane = pane.focused if pane is not None else None
        if pane is not None and pane.component is not None:
            pane.component.focus_default()

    def _area(self):
        """The part of the window the panel may cover: the workspace, left of
        the help panel. None without a workspace."""
        window = self.window()
        workspace = getattr(window, "workspace", None)
        if workspace is None:
            return None
        area = QRect(workspace.mapTo(window, QPoint(0, 0)), workspace.size())
        help_panel = getattr(window, "_help", None)
        if help_panel is not None and help_panel.isVisible():
            area.setRight(min(area.right(), help_panel.geometry().left() - 1))
        return area

    def _corners(self, area):
        """Where the panel may go, in place()'s order of preference: bottom
        right, bottom left, top right, top left."""
        width = max(min(PANEL_WIDTH, int(area.width() * 0.45)), min(280, area.width()))
        # As tall as the content wants up to 55% of the workspace, and taller
        # when the sense list would show fewer than LIST_ROWS rows.
        wanted = self.heightForWidth(width)
        if wanted < 0:
            wanted = self.sizeHint().height()
        rows = min(LIST_ROWS, max(self.list.count(), 1))
        row = self.list.sizeHintForRow(0) if self.list.count() else self.list.fontMetrics().height()
        floor = rows * row + 2 * self.list.frameWidth()
        margins, inner = self.layout().contentsMargins(), self.contentsMargins()
        across = (width - margins.left() - margins.right() - inner.left() - inner.right()
                  - 2 * self.list.frameWidth())
        if self.list.count() and self.list.sizeHintForColumn(0) > across:
            floor += self.list.horizontalScrollBar().sizeHint().height()  # long rows scroll
        floor += wanted - self.list.sizeHint().height()
        room = area.height() - MARGIN - BOTTOM_MARGIN
        height = max(min(wanted, int(area.height() * 0.55)), floor)
        height = max(min(height, room), min(180, area.height()))
        left, right = area.left() + MARGIN, area.right() - MARGIN - width
        top, bottom = area.top() + MARGIN, area.bottom() - BOTTOM_MARGIN - height
        return [QRect(x, y, width, height) for y in (bottom, top) for x in (right, left)]

    def fit_contents(self) -> None:
        """The content changed (a reply, a reloaded list): give the panel the
        height it needs now, in the corner place() chose."""
        area = self._area() if self.isVisible() else None
        if area is not None:
            self.setGeometry(self._corners(area)[self._corner])

    def place(self) -> None:
        """Put the panel in a corner of the workspace, left of the help panel,
        avoiding the focused pane where it can and the text cursor."""
        area = self._area()
        if area is None:
            return
        window = self.window()
        workspace = window.workspace
        corners = self._corners(area)
        avoid = []
        focus = window.focusWidget()
        if focus is not None and not self.isAncestorOf(focus):
            pane = workspace.focused
            if pane is not None and len(getattr(workspace, "_panes", ())) > 1:
                avoid.append(QRect(pane.mapTo(window, QPoint(0, 0)), pane.size()))
            caret = focus.inputMethodQuery(Qt.ImCursorRectangle)
            if isinstance(caret, (QRect, QRectF)):
                caret = QRectF(caret).toRect()
                if caret.width() or caret.height():
                    avoid.append(QRect(focus.mapTo(window, caret.topLeft()),
                                       caret.size()).adjusted(-4, -4, 4, 4))
        choice = corners[0]
        for keep_clear in avoid:
            free = [corner for corner in corners if not corner.intersects(keep_clear)]
            if free:
                choice = free[0]
                break
        self._corner = corners.index(choice)
        self.setGeometry(choice)
