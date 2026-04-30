"""Tiny grid widget that adapts its column count to its available width.

Used by OverviewTab to fold the summary / recent / stat areas into single
columns on narrow windows (spec §5.2).

Pass a sorted list of ``(min_width, cols)`` breakpoints in descending
order. The widget picks the first breakpoint whose ``min_width`` is
``<=`` its current width; falls back to 1 column.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QGridLayout, QWidget


class ReflowGrid(QWidget):
    def __init__(
        self,
        breakpoints: list[tuple[int, int]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._breakpoints = sorted(breakpoints, reverse=True)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._children: list[QWidget] = []
        self._cols = 0

    def add_widget(self, widget: QWidget, *, stretch: int = 0) -> None:
        widget.setParent(self)
        self._children.append(widget)
        self._apply(force=True)
        if stretch:
            row = (len(self._children) - 1) // max(self._cols, 1)
            self._grid.setRowStretch(row, stretch)

    def resizeEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply()

    def _cols_for(self, width: int) -> int:
        for min_w, cols in self._breakpoints:
            if width >= min_w:
                return cols
        return 1

    def _apply(self, *, force: bool = False) -> None:
        cols = self._cols_for(self.width())
        if cols == self._cols and not force:
            return
        self._cols = cols
        for child in self._children:
            self._grid.removeWidget(child)
        for index, child in enumerate(self._children):
            row = index // cols
            col = index % cols
            self._grid.addWidget(child, row, col)
        # Reset col stretch so the grid spreads evenly.
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 1 if c < cols else 0)
