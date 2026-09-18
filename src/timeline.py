"""Two-handled range selection with a seekable playhead."""
from i18n import tr
from PySide6.QtCore import Qt, Signal, QRect, QPoint
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget
from media import format_time


class RangeTimeline(QWidget):
    changed = Signal(float, float)
    seek = Signal(float)
    committed = Signal()
    activate = Signal(int)
    viewChanged = Signal(float, float)
    contextRequested = Signal(int, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bounds = (0., 1.)
        self.selection = (0., 0.)
        self.position = 0.
        self.drag = None
        self.extent = 1.
        self.manual_view = False
        self.clips = []
        self.custom_ids = set()
        self.active_clip = -1
        self.viewport = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumHeight(88)
        self.setToolTip(tr('클릭·드래그: 탐색 | 양끝: 트리밍 | Ctrl+드래그: 구간 이동\n휠: 시간축 이동 | Ctrl+휠: 확대 | 중간 버튼 드래그: 이동 | Alt: 스냅 해제'))
        self.setToolTip(self.toolTip() + tr('\n윗줄: 자동 클립 / 아랫줄(보라색): 수동 추가 · 우클릭: 제거/추가'))
        self.setAccessibleName(tr('시간 범위: 양 끝 핸들을 드래그해 조절'))

    def set_values(self, low, high, start, end, position):
        self.extent = max(self.extent, high, end)
        if self.drag is None and not self.manual_view:
            self.bounds = (low, max(low + .001, high))
        self.selection, self.position = (start, end), position
        self.viewChanged.emit(*self.bounds)
        self.update()

    def view(self, low, high):
        width = min(self.extent, max(.1, high - low))
        low = max(0, min(self.extent - width, low))
        self.bounds = (low, low + width)
        self.manual_view = True
        self.viewChanged.emit(*self.bounds)
        self.update()

    def zoom(self, factor, anchor=None):
        low, high = self.bounds
        anchor = self.position if anchor is None else anchor
        anchor = max(low, min(high, anchor))
        self.view(anchor - (anchor - low) / factor, anchor + (high - anchor) / factor)

    def fit(self):
        self.view(0, self.extent)

    def x(self, seconds):
        low, high = self.bounds
        return 12 + (self.width() - 24) * min(1, max(0, (seconds - low) / (high - low)))

    def time(self, x):
        low, high = self.bounds
        return low + min(1, max(0, (x - 12) / max(1, self.width() - 24))) * (high - low)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(12, 12, max(1, self.width() - 24), 12, self.palette().mid())
        left, right = (round(self.x(value)) for value in self.selection)
        painter.fillRect(left, 12, max(1, right - left), 12, QColor("#389fc4"))
        painter.setPen(QPen(QColor("#17779a"), 4))
        for x in (left, right):
            painter.drawLine(x, 7, x, 29)
        painter.setPen(QPen(QColor("#e18b2e"), 2))
        x = round(self.x(self.position))
        painter.drawLine(x, 3, x, 31)
        for index, start, end, title in self.clips:
            if end < self.bounds[0] or start > self.bounds[1]:
                continue
            a, b = round(self.x(start)), round(self.x(end))
            y = 54 if index in self.custom_ids else 34
            painter.fillRect(a, y, max(1, b - a - 1), 14, QColor("#8865aa" if index in self.custom_ids else "#447c92" if index % 2 else "#588fa4"))
            if index == self.active_clip:
                painter.setPen(QPen(QColor("#ffc857"), 2))
                painter.drawRect(a, y - 1, max(1, b - a - 1), 16)
            painter.setPen(QColor("white"))
            text = painter.fontMetrics().elidedText(f"{index + 1} · {title or tr('곡 미확인')}", Qt.TextElideMode.ElideRight, max(0, b - a - 6))
            painter.drawText(QRect(a + 3, y - 1, max(1, b - a - 6), 16), Qt.AlignmentFlag.AlignVCenter, text)
        if self.viewport:
            a, b = (round(self.x(value)) for value in self.viewport)
            painter.fillRect(a, 1, max(1, b - a), 29, QColor(147, 99, 211, 40))
            painter.setPen(QPen(QColor("#9255c9"), 2, Qt.PenStyle.DashLine))
            painter.drawRect(a, 1, max(1, b - a), 29)
        painter.setPen(self.palette().text().color())
        painter.drawText(12, 85, format_time(self.bounds[0]))
        text = format_time(self.bounds[1])
        painter.drawText(self.width() - 12 - painter.fontMetrics().horizontalAdvance(text), 85, text)

    def clip_at(self, x, y):
        if not 32 <= y < 72:
            return -1
        custom = y >= 52
        return next((index for index, start, end, _ in reversed(self.clips)
                     if (index in self.custom_ids) == custom and start <= self.time(x) < end), -1)

    def contextMenuEvent(self, event):
        self.contextRequested.emit(self.clip_at(event.pos().x(), event.pos().y()), event.globalPos())
        event.accept()

    def mousePressEvent(self, event):
        self.setFocus()
        x = event.position().x()
        self.press_x, self.press_selection, self.press_bounds = x, self.selection, self.bounds
        if event.button() == Qt.MouseButton.MiddleButton:
            self.drag = "pan"
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self.clip_at(x, event.position().y())
        if index >= 0:
            self.activate.emit(index)
            return
        distances = [abs(x - self.x(value)) if self.bounds[0] <= value <= self.bounds[1] else float("inf") for value in self.selection]
        if min(distances) <= 10:
            self.drag = distances.index(min(distances))
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and self.selection[0] <= self.time(x) <= self.selection[1]:
            self.drag = "move"
        else:
            self.drag = "seek"
            self.seek.emit(self.time(x))

    def mouseMoveEvent(self, event):
        if self.drag is None:
            self.setCursor(Qt.CursorShape.SizeHorCursor if min(abs(event.position().x() - self.x(v)) for v in self.selection) <= 10 else Qt.CursorShape.ArrowCursor)
            return
        x = event.position().x()
        if self.drag == "pan":
            low, high = self.press_bounds
            offset = (x - self.press_x) / max(1, self.width() - 24) * (high - low)
            self.view(low - offset, high - offset)
            return
        if self.drag == "seek":
            self.seek.emit(self.time(x))
            return
        start, end = self.selection
        value = self.time(x)
        if not event.modifiers() & Qt.KeyboardModifier.AltModifier:
            targets = [0, self.extent, self.position] + [t for _, a, b, _ in self.clips for t in (a, b)]
            nearest = min(targets, key=lambda t: abs(self.x(t) - x))
            if self.bounds[0] <= nearest <= self.bounds[1] and abs(self.x(nearest) - x) <= 6:
                value = nearest
        if self.drag == "move":
            a, b = self.press_selection
            delta = self.time(x) - self.time(self.press_x)
            delta = max(-a, min(self.extent - b, delta))
            start, end = a + delta, b + delta
        elif self.drag == 0:
            start = min(value, end - .001)
        else:
            end = max(value, start + .001)
        self.selection = (max(0, start), min(self.extent, end))
        self.changed.emit(*self.selection)
        self.update()

    def mouseReleaseEvent(self, event):
        edited = self.drag in (0, 1, "move")
        self.drag = None
        if edited:
            self.committed.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta:
            delta = event.pixelDelta().y() or event.pixelDelta().x()
        steps = delta / 120
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.25 ** steps, self.time(event.position().x()))
        else:
            low, high = self.bounds
            offset = -steps * (high - low) * (.4 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else .1)
            self.view(low + offset, high + offset)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.fit()
