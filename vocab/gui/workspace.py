"""A tmux-style tiling pane manager built on nested QSplitters.

Model
-----
* A **Pane** wraps exactly one component (Vocab/Notes/Documents/Console/Study).
  It has a title bar that highlights when the pane is focused, and it can cycle
  which component it shows (Ctrl+Tab).
* The **Workspace** owns the tree of panes. Splitting a pane replaces it in its
  parent QSplitter with a new QSplitter holding [original, new]. Closing a pane
  collapses the splitter. Zoom temporarily swaps the whole tree for a single
  maximized pane, restoring the tree on toggle-off.

All navigation is keyboard-driven; the Workspace exposes methods the MainWindow
binds to global shortcuts. Focus is tracked by geometry so Alt+H/J/K/L move to
the spatially nearest pane.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSplitter,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import theme


class Pane(QFrame):
    """One tile: a title bar + a single component widget."""

    focus_requested = Signal(object)   # emits self when this pane should focus

    def __init__(self, component, factory_key: str, workspace: "Workspace"):
        super().__init__()
        self.workspace = workspace
        self.component = component
        self.factory_key = factory_key
        self.setObjectName("Pane")
        self._focused = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.title = QLabel(getattr(component, "TITLE", "Pane"))
        self.title.setObjectName("PaneTitle")
        lay.addWidget(self.title)
        lay.addWidget(component, 1)
        self._apply_focus_style()

    def set_component(self, component, factory_key: str) -> None:
        old = self.component
        # let the outgoing component persist any state (e.g. reading position)
        hook = getattr(old, "on_replaced", None)
        if callable(hook):
            try:
                hook()
            except Exception:
                pass
        self.layout().replaceWidget(old, component)
        old.setParent(None)
        old.deleteLater()
        self.component = component
        self.factory_key = factory_key
        self.title.setText(getattr(component, "TITLE", "Pane"))

    def set_focused(self, on: bool) -> None:
        """Highlight AND (when on) move keyboard focus into the component.

        Used for programmatic focus (Alt+nav, splits, pane N). For highlight-only
        sync from QApplication.focusChanged, use set_focused_highlight().
        """
        self.set_focused_highlight(on)
        if on:
            fd = getattr(self.component, "focus_default", None)
            if callable(fd):
                fd()

    def set_focused_highlight(self, on: bool) -> None:
        """Update only the visual focus highlight; do not touch keyboard focus."""
        self._focused = on
        self._apply_focus_style()

    def _apply_focus_style(self) -> None:
        border = theme.WALNUT if self._focused else theme.RULE
        title_bg = theme.WALNUT if self._focused else theme.BG_PANEL
        title_fg = theme.LINEN if self._focused else theme.INK_DIM
        self.setStyleSheet(
            f"#Pane {{ border: 1px solid {border}; border-radius: 8px; }}"
            f"#PaneTitle {{ background: {title_bg}; color: {title_fg};"
            f" padding: 4px 8px; border-top-left-radius: 7px;"
            f" border-top-right-radius: 7px; font-weight: bold; }}")

    def center(self):
        g = self.rect()
        return self.mapToGlobal(g.center())


class Workspace(QWidget):
    """Holds the pane tree and drives all pane operations."""

    def __init__(self, make_component: Callable[[str], object],
                 component_order: List[str], parent=None):
        super().__init__(parent)
        # make_component(key) -> fresh component widget; component_order = cycle list
        self._make = make_component
        self._order = component_order
        self._panes: List[Pane] = []
        self._focused: Optional[Pane] = None
        self._zoom_saved = None   # (root_splitter) while zoomed

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        self._outer.addWidget(self._stack)

        # page 0: the tiled tree
        self._tree_page = QWidget()
        tree_lay = QVBoxLayout(self._tree_page)
        tree_lay.setContentsMargins(0, 0, 0, 0)
        self.root = QSplitter(Qt.Horizontal)
        tree_lay.addWidget(self.root)
        self._stack.addWidget(self._tree_page)

        # page 1: the zoom container (a single maximized pane)
        self._zoom_page = QWidget()
        zlay = QVBoxLayout(self._zoom_page)
        zlay.setContentsMargins(0, 0, 0, 0)
        self._zoom_lay = zlay
        self._stack.addWidget(self._zoom_page)

        self._zoomed_pane = None   # Pane currently zoomed, or None

        first = self._new_pane(component_order[0])
        self.root.addWidget(first)
        self.focus_pane(first)

    # ---- pane creation --------------------------------------------------
    def _new_pane(self, key: str) -> Pane:
        comp = self._make(key)
        pane = Pane(comp, key, self)
        pane.focus_requested.connect(self.focus_pane)
        self._panes.append(pane)
        return pane

    # ---- focus ----------------------------------------------------------
    def focus_pane(self, pane: Pane) -> None:
        if pane is None or pane not in self._panes:
            return
        for p in self._panes:
            if p is not pane and p._focused:
                p.set_focused(False)
        self._focused = pane
        pane.set_focused(True)

    @property
    def focused(self) -> Optional[Pane]:
        return self._focused

    def pane_containing(self, widget) -> Optional[Pane]:
        """Return the pane whose component subtree contains `widget`, or None."""
        w = widget
        while w is not None:
            for p in self._panes:
                if w is p:
                    return p
            w = w.parentWidget()
        return None

    def sync_focus_to_widget(self, widget) -> None:
        """Highlight the pane that owns `widget` WITHOUT re-grabbing focus.

        Used by QApplication.focusChanged so clicking into any control (or the
        keyboard moving focus) keeps the pane highlight in sync. We must NOT
        call the component's focus_default() here, or we'd fight the user's
        actual cursor — so this is a highlight-only update.
        """
        pane = self.pane_containing(widget)
        if pane is None or pane is self._focused:
            return
        for p in self._panes:
            if p._focused and p is not pane:
                p.set_focused_highlight(False)
        self._focused = pane
        pane.set_focused_highlight(True)

    def focus_index(self, n: int) -> None:
        """Focus pane number n (1-based) in creation/visual order."""
        visible = [p for p in self._panes if p.isVisible()]
        if 1 <= n <= len(visible):
            self.focus_pane(visible[n - 1])

    def move_focus(self, direction: str) -> None:
        """Move focus to the spatially nearest pane in a direction (h/j/k/l)."""
        if not self._focused:
            return
        cur = self._focused
        cur_c = cur.mapTo(self, cur.rect().center())
        best, best_d = None, None
        for p in self._panes:
            if p is cur or not p.isVisible():
                continue
            c = p.mapTo(self, p.rect().center())
            dx, dy = c.x() - cur_c.x(), c.y() - cur_c.y()
            ok = {
                "l": dx > 10, "h": dx < -10, "j": dy > 10, "k": dy < -10,
            }[direction]
            if not ok:
                continue
            # primary axis distance + small penalty for cross-axis drift
            if direction in ("l", "h"):
                dist = abs(dx) + abs(dy) * 2
            else:
                dist = abs(dy) + abs(dx) * 2
            if best_d is None or dist < best_d:
                best, best_d = p, dist
        if best:
            self.focus_pane(best)

    # ---- splitting ------------------------------------------------------
    def split(self, orientation: Qt.Orientation) -> None:
        if self.is_zoomed:
            self.toggle_zoom()  # splitting while zoomed first un-zooms
        cur = self._focused
        if cur is None:
            return
        parent = cur.parentWidget()
        # parent is a QSplitter
        if not isinstance(parent, QSplitter):
            return
        idx = parent.indexOf(cur)
        new_pane = self._new_pane(cur.factory_key_next_default())

        if parent.orientation() == orientation:
            # same orientation: insert a sibling, then distribute evenly
            parent.insertWidget(idx + 1, new_pane)
            self._even_out(parent)
        else:
            # different orientation: wrap current pane in a new splitter and
            # give the two children equal halves of the space cur occupied.
            outer_sizes = parent.sizes()
            new_split = QSplitter(orientation)
            parent.insertWidget(idx, new_split)   # placeholder position
            new_split.addWidget(cur)              # reparents cur into new_split
            new_split.addWidget(new_pane)
            # restore the outer splitter's distribution (unchanged slot count)
            if outer_sizes:
                parent.setSizes(outer_sizes)
            self._even_out(new_split)
        self.focus_pane(new_pane)

    @staticmethod
    def _even_out(splitter: QSplitter) -> None:
        """Distribute the splitter's current extent equally across its children.

        Uses the splitter's real pixel extent along its orientation so the panes
        come out visually 50/50 (n children -> n equal shares). Deferred a tick
        because a freshly-inserted splitter may not have its final geometry yet.
        """
        from PySide6.QtCore import QTimer

        def apply():
            from shiboken6 import isValid
            if not isValid(splitter):
                return
            n = splitter.count()
            if n <= 0:
                return
            extent = (splitter.width() if splitter.orientation() == Qt.Horizontal
                      else splitter.height())
            if extent <= 0:
                extent = 1000 * n  # sane fallback before first layout
            share = max(1, extent // n)
            splitter.setSizes([share] * n)

        apply()             # immediate best-effort
        QTimer.singleShot(0, apply)   # and again once geometry settles

    def split_vertical(self) -> None:
        # visually side-by-side => a Horizontal splitter
        self.split(Qt.Horizontal)

    def split_horizontal(self) -> None:
        # visually stacked => a Vertical splitter
        self.split(Qt.Vertical)

    # ---- close ----------------------------------------------------------
    def close_focused(self) -> None:
        if self.is_zoomed:
            self.toggle_zoom()
        if len(self._panes) <= 1:
            return  # never close the last pane
        cur = self._focused
        parent = cur.parentWidget()
        self._panes.remove(cur)
        neighbor = None
        if isinstance(parent, QSplitter):
            idx = parent.indexOf(cur)
            neighbor_idx = idx - 1 if idx > 0 else idx + 1
            if 0 <= neighbor_idx < parent.count():
                w = parent.widget(neighbor_idx)
                neighbor = self._first_pane_in(w)
        hook = getattr(cur.component, "on_replaced", None)
        if callable(hook):
            hook()
        cur.setParent(None)
        cur.deleteLater()
        self._collapse_empty_splitters()
        self.focus_pane(neighbor or (self._panes[0] if self._panes else None))

    def _first_pane_in(self, w) -> Optional[Pane]:
        if isinstance(w, Pane):
            return w
        if isinstance(w, QSplitter):
            for i in range(w.count()):
                p = self._first_pane_in(w.widget(i))
                if p:
                    return p
        return None

    def _collapse_empty_splitters(self) -> None:
        # remove splitters that have a single child by promoting the child
        changed = True
        while changed:
            changed = False
            for sp in self.findChildren(QSplitter):
                if sp is self.root:
                    continue
                if sp.count() == 1:
                    child = sp.widget(0)
                    parent = sp.parentWidget()
                    if isinstance(parent, QSplitter):
                        idx = parent.indexOf(sp)
                        sizes = parent.sizes()
                        parent.insertWidget(idx, child)
                        sp.setParent(None)
                        sp.deleteLater()
                        if sizes:
                            parent.setSizes(sizes)
                        changed = True
                        break

    # ---- zoom -----------------------------------------------------------
    def toggle_zoom(self) -> None:
        if self._zoomed_pane is None:
            cur = self._focused
            if cur is None:
                return
            parent = cur.parentWidget()
            if not isinstance(parent, QSplitter):
                return
            self._zoom_origin = (parent, parent.indexOf(cur), parent.sizes())
            self._zoom_lay.addWidget(cur)   # reparents cur into the zoom page
            self._zoomed_pane = cur
            self._stack.setCurrentWidget(self._zoom_page)
            self.focus_pane(cur)
        else:
            cur = self._zoomed_pane
            parent, idx, sizes = self._zoom_origin
            parent.insertWidget(idx, cur)   # reparents back into the tree
            if sizes:
                parent.setSizes(sizes)
            self._zoomed_pane = None
            self._zoom_origin = None
            self._stack.setCurrentWidget(self._tree_page)
            self.focus_pane(cur)

    @property
    def is_zoomed(self) -> bool:
        return self._zoomed_pane is not None

    # ---- component cycling ---------------------------------------------
    def cycle_component(self, delta: int = 1) -> None:
        cur = self._focused
        if cur is None:
            return
        try:
            i = self._order.index(cur.factory_key)
        except ValueError:
            i = 0
        key = self._order[(i + delta) % len(self._order)]
        cur.set_component(self._make(key), key)
        cur.set_focused(True)

    def set_focused_component(self, key: str) -> None:
        cur = self._focused
        if cur is None:
            return
        cur.set_component(self._make(key), key)
        cur.set_focused(True)

    def snapshot(self, describe):
        """Serialize the logical tree, including the slot temporarily zoomed out."""
        def encode(widget):
            if isinstance(widget, Pane):
                return {"pane": describe(widget), "order": self._panes.index(widget)}
            children = [widget.widget(i) for i in range(widget.count())]
            sizes = widget.sizes()
            if self.is_zoomed and widget is self._zoom_origin[0]:
                _, index, sizes = self._zoom_origin
                children.insert(index, self._zoomed_pane)
            return {"direction": "h" if widget.orientation() == Qt.Horizontal else "v",
                    "sizes": sizes, "children": [encode(child) for child in children]}
        return {"tree": encode(self.root), "focused": self._panes.index(self._focused),
                "zoomed": self.is_zoomed}

    def restore(self, state, configure):
        """Restore validated layout data; transient panes are mapped by the caller."""
        count = 0
        def validate(node, depth=0):
            nonlocal count
            if not isinstance(node, dict) or depth > 20:
                raise ValueError("Invalid saved workspace layout.")
            if "pane" in node:
                count += 1
                if count > 32 or not isinstance(node["pane"], dict):
                    raise ValueError("Invalid saved workspace pane.")
                return
            children = node.get("children")
            if not isinstance(children, list) or not children or len(children) > 32:
                raise ValueError("Invalid saved workspace split.")
            sizes = node.get("sizes", [])
            if not isinstance(sizes, list) or not all(isinstance(n, int) and 0 <= n < 100000 for n in sizes):
                raise ValueError("Invalid saved workspace sizes.")
            for child in children:
                validate(child, depth + 1)
        tree = state["tree"]
        validate(tree)
        if "pane" in tree:
            raise ValueError("Saved workspace must have a root split.")
        if self.is_zoomed:
            self.toggle_zoom()
        old_root = self.root
        self._panes = []
        self._focused = None
        ordered = []
        def build(node):
            if "pane" in node:
                pane = self._new_pane(self._order[0])
                configure(pane, node["pane"])
                order = node.get("order", len(ordered))
                ordered.append((order if isinstance(order, int) else len(ordered), pane))
                return pane
            splitter = QSplitter(Qt.Horizontal if node.get("direction") == "h" else Qt.Vertical)
            for child in node["children"]:
                splitter.addWidget(build(child))
            if len(node.get("sizes", [])) == splitter.count():
                splitter.setSizes(node["sizes"])
            return splitter
        self.root = build(tree)
        self._panes = [pane for _, pane in sorted(ordered, key=lambda pair: pair[0])]
        self._tree_page.layout().replaceWidget(old_root, self.root)
        old_root.setParent(None)
        old_root.deleteLater()
        focused = state.get("focused", 0)
        focused = focused if isinstance(focused, int) else 0
        self.focus_pane(self._panes[max(0, min(focused, len(self._panes) - 1))])
        if state.get("zoomed"):
            self.toggle_zoom()


# Small helper on Pane used by split(): the new pane defaults to the same
# component as the one being split (so a split shows two of the same until you
# cycle one). Defined here to keep Pane lean.
def _factory_key_next_default(self):
    return self.factory_key


Pane.factory_key_next_default = _factory_key_next_default
