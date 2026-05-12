from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.ui.widgets import CollapsibleSection, InlineCollapsibleSection


class AutoGrowingTextBrowser(QTextBrowser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resize_margin = 10
        self._resizing = False
        self._resize_start_y = 0
        self._initial_height = 0
        self._base_height = 60
        self._manual_min_height = 0
        self.setMinimumHeight(60)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.textChanged.connect(self._update_height_to_contents)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._update_height_to_contents()
        )

    def set_base_height(self, height: int):
        self._base_height = max(self.minimumHeight(), height)
        self._update_height_to_contents()

    def _update_height_to_contents(self):
        doc_layout = self.document().documentLayout()
        doc_height = int(doc_layout.documentSize().height()) if doc_layout is not None else 0
        if doc_height <= 0:
            line_height = self.fontMetrics().lineSpacing()
            doc_height = max(1, self.document().blockCount()) * line_height
        margins = self.contentsMargins()
        frame = self.frameWidth() * 2
        padding = margins.top() + margins.bottom() + 18
        target_height = max(self._base_height, self._manual_min_height, doc_height + frame + padding)
        self.setFixedHeight(target_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_height_to_contents)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() >= self.viewport().height() - self._resize_margin:
            self._resizing = True
            self._resize_start_y = int(event.globalPosition().y())
            self._initial_height = self.height()
            self.viewport().setCursor(Qt.SizeVerCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            delta = int(event.globalPosition().y()) - self._resize_start_y
            self._manual_min_height = max(self.minimumHeight(), self._initial_height + delta)
            self._update_height_to_contents()
            event.accept()
            return
        if event.position().y() >= self.viewport().height() - self._resize_margin:
            self.viewport().setCursor(Qt.SizeVerCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing and event.button() == Qt.LeftButton:
            self._resizing = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class OutputBlockManager:
    def __init__(
        self,
        scroll_area: QScrollArea,
        layout: QVBoxLayout,
        *,
        on_insert_to_main: Optional[Callable[[str], None]] = None,
        on_append_to_memo: Optional[Callable[[str], None]] = None,
        on_status_message: Optional[Callable[[str, int], None]] = None,
    ):
        self.scroll_area = scroll_area
        self.layout = layout
        self.on_insert_to_main = on_insert_to_main
        self.on_append_to_memo = on_append_to_memo
        self.on_status_message = on_status_message
        self._block_widgets: list[QWidget] = []
        self._last_selection = ""
        self._auto_follow = True

    def create_block(self, title: str, *, include_thinking: bool) -> tuple[QTextBrowser, Optional[QTextBrowser]]:
        should_follow = self.should_auto_scroll()
        block_widget = InlineCollapsibleSection(title)
        block_widget.setObjectName("outputBlock")
        block_widget.toggle_button.setObjectName("outputBlockTitle")
        block_layout = block_widget.content_layout
        block_layout.setContentsMargins(0, 0, 0, 12)
        block_layout.setSpacing(6)

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(4, 2, 4, 0)
        action_layout.setSpacing(6)

        thinking_editor: Optional[QTextBrowser] = None
        if include_thinking:
            thinking_section = InlineCollapsibleSection("思考")
            thinking_section.setObjectName("outputBlockThinking")
            thinking_section.toggle_button.setObjectName("outputBlockThinkingTitle")
            thinking_editor = AutoGrowingTextBrowser()
            thinking_editor.setPlaceholderText("思考ログ")
            thinking_editor.setObjectName("outputBlockThinkingEditor")
            thinking_editor.set_base_height(84)
            setattr(thinking_editor, "_thinking_section", thinking_section)
            self.register_selection_tracking(thinking_editor)
            thinking_section.addWidget(thinking_editor)
            thinking_section.set_expanded(False)

        content_editor = AutoGrowingTextBrowser()
        content_editor.setPlaceholderText("出力")
        content_editor.setObjectName("outputBlockContent")
        content_editor.set_base_height(96)
        self.register_selection_tracking(content_editor)

        insert_button = self._create_action_button("本文へ挿入")
        memo_button = self._create_action_button("メモへ送る")
        copy_button = self._create_action_button("コピー")
        delete_button = self._create_action_button("削除")
        insert_button.clicked.connect(lambda _checked=False, editor=content_editor: self._insert_to_main(editor))
        memo_button.clicked.connect(lambda _checked=False, editor=content_editor: self._append_to_memo(editor))
        copy_button.clicked.connect(lambda _checked=False, editor=content_editor: self._copy_text(editor))
        delete_button.clicked.connect(lambda _checked=False, block=block_widget: self._delete_block(block))
        action_layout.addWidget(insert_button)
        action_layout.addWidget(memo_button)
        action_layout.addWidget(copy_button)
        action_layout.addStretch()
        action_layout.addWidget(delete_button)

        block_layout.addLayout(action_layout)
        if thinking_editor is not None:
            block_layout.addWidget(thinking_section)
        block_layout.addWidget(content_editor)

        insert_index = max(0, self.layout.count() - 1)
        self.layout.insertWidget(insert_index, block_widget)
        self._block_widgets.append(block_widget)
        self.apply_style(block_widget)
        block_widget.set_expanded(True)
        block_widget.refresh_content_height()
        if should_follow:
            QTimer.singleShot(0, self.scroll_to_bottom)
        return content_editor, thinking_editor

    def _create_action_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.NoFocus)
        button.setObjectName("outputBlockActionButton")
        return button

    def _card_action_text(self, editor: QTextBrowser) -> str:
        selected = self.selected_text_from_widget(editor)
        return selected or editor.toPlainText()

    def _show_status(self, message: str, timeout: int = 2000):
        if self.on_status_message is not None:
            self.on_status_message(message, timeout)

    def _insert_to_main(self, editor: QTextBrowser):
        text = self._card_action_text(editor)
        if not text:
            self._show_status("候補に転記できるテキストがありません。")
            return
        if self.on_insert_to_main is not None:
            self.on_insert_to_main(text)
        self._last_selection = text

    def _append_to_memo(self, editor: QTextBrowser):
        text = self._card_action_text(editor)
        if not text:
            self._show_status("候補にメモへ送れるテキストがありません。")
            return
        if self.on_append_to_memo is not None:
            self.on_append_to_memo(text)
        self._last_selection = text

    def _copy_text(self, editor: QTextBrowser):
        text = self._card_action_text(editor)
        if not text:
            self._show_status("コピーできるテキストがありません。")
            return
        QApplication.clipboard().setText(text)
        self._last_selection = text
        self._show_status("候補テキストをコピーしました。")

    def _delete_block(self, block_widget: QWidget):
        # Keep the widget alive in case a generation stream still owns its editors.
        block_widget.hide()
        self._show_status("候補カードを非表示にしました。")

    def append_to_editor(self, editor: Optional[QTextBrowser], text: str):
        if editor is None or not text:
            return
        should_follow = self.should_auto_scroll()
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        editor.setTextCursor(cursor)
        self.refresh_block_heights(editor)
        if should_follow:
            QTimer.singleShot(0, self.scroll_to_bottom)

    def set_thinking_title_streaming(self, editor: Optional[QTextBrowser], active: bool):
        if editor is None:
            return
        thinking_section = getattr(editor, "_thinking_section", None)
        if thinking_section is not None and hasattr(thinking_section, "set_streaming_active"):
            thinking_section.set_streaming_active(active)

    def clear(self):
        while self.layout.count() > 1:
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._block_widgets.clear()
        self._last_selection = ""

    def should_auto_scroll(self) -> bool:
        return bool(self._auto_follow)

    def scroll_to_bottom(self):
        v_bar = self.scroll_area.verticalScrollBar()
        v_bar.setValue(v_bar.maximum())

    def update_auto_follow(self):
        v_bar = self.scroll_area.verticalScrollBar()
        self._auto_follow = v_bar.value() >= v_bar.maximum() - 12

    def on_scroll_changed(self, *_args):
        self.update_auto_follow()

    def on_range_changed(self, *_args):
        if self.should_auto_scroll():
            QTimer.singleShot(0, self.scroll_to_bottom)
            QTimer.singleShot(16, self.scroll_to_bottom)

    def apply_style(self, block_widget: Optional[QWidget] = None):
        blocks = [block_widget] if block_widget is not None else list(self._block_widgets)
        palette = QApplication.palette()
        window = palette.color(QPalette.Window)
        base = palette.color(QPalette.Base)
        button = palette.color(QPalette.Button)
        text = palette.color(QPalette.Text)
        mid = palette.color(QPalette.Mid)

        def soften(color: QColor, amount: int, lighter: bool) -> str:
            tuned = color.lighter(amount) if lighter else color.darker(amount)
            return tuned.name()

        dark_ui = window.lightness() < 128
        block_bg = soften(window, 106 if dark_ui else 102, lighter=not dark_ui)
        title_bg = soften(button, 112 if dark_ui else 103, lighter=not dark_ui)
        editor_bg = soften(base, 103 if dark_ui else 100, lighter=not dark_ui)
        border = soften(mid, 125 if dark_ui else 110, lighter=not dark_ui)
        muted_text = text.name()

        block_style = (
            "QWidget#outputBlock {"
            f" background-color: {block_bg};"
            f" border: 1px solid {border};"
            " border-radius: 8px;"
            " padding: 6px;"
            " margin-top: 4px;"
            "}"
            "QToolButton#outputBlockTitle {"
            f" background-color: {title_bg};"
            f" color: {muted_text};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 7px 10px;"
            " font-weight: 600;"
            " text-align: left;"
            "}"
            "QToolButton#outputBlockThinkingTitle {"
            f" background-color: {editor_bg};"
            f" color: {muted_text};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 6px 10px;"
            " text-align: left;"
            "}"
            "QToolButton#outputBlockThinkingTitle[streaming=\"true\"] {"
            " background-color: #fff0b3;"
            " color: #4d3600;"
            " border-color: #d69b00;"
            " font-weight: 600;"
            "}"
            "QToolButton#outputBlockThinkingTitle[streaming=\"true\"][pulse=\"true\"] {"
            " background-color: #ffd86b;"
            "}"
            "QTextBrowser#outputBlockContent, QTextBrowser#outputBlockThinkingEditor {"
            f" background-color: {editor_bg};"
            f" color: {muted_text};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 8px;"
            " selection-background-color: palette(highlight);"
            "}"
            "QPushButton#outputBlockActionButton {"
            f" background-color: {editor_bg};"
            f" color: {muted_text};"
            f" border: 1px solid {border};"
            " border-radius: 4px;"
            " padding: 4px 8px;"
            "}"
            "QPushButton#outputBlockActionButton:hover {"
            f" background-color: {title_bg};"
            "}"
        )
        for widget in blocks:
            if widget is not None:
                widget.setStyleSheet(block_style)

    def refresh_block_heights(self, widget: Optional[QWidget]):
        current = widget
        while current is not None:
            if isinstance(current, (CollapsibleSection, InlineCollapsibleSection)):
                current.refresh_content_height()
            current = current.parentWidget()

    def register_selection_tracking(self, widget: QTextBrowser):
        widget.copyAvailable.connect(
            lambda available, source=widget: self.capture_selection(source) if available else None
        )
        widget.selectionChanged.connect(lambda source=widget: self.capture_selection(source))

    def capture_selection(self, widget: QWidget):
        selected = self.selected_text_from_widget(widget)
        if selected:
            self._last_selection = selected

    def selected_text_from_widget(self, widget: Optional[QWidget]) -> str:
        text_widget = self.find_text_selection_source(widget)
        if text_widget is None:
            return ""
        selected = text_widget.textCursor().selectedText()
        if not selected:
            return ""
        return selected.replace("\u2029", "\n")

    def find_text_selection_source(self, widget: Optional[QWidget]) -> Optional[QWidget]:
        current = widget
        while current is not None:
            if isinstance(current, (QTextBrowser, QPlainTextEdit)):
                return current
            current = current.parentWidget()
        return None

    def get_selected_output_text(
        self,
        focus_widget: Optional[QWidget],
        fallback_widget: Optional[QPlainTextEdit] = None,
    ) -> str:
        selected = self.selected_text_from_widget(focus_widget)
        if selected:
            self._last_selection = selected
            return selected
        if self._last_selection:
            return self._last_selection
        if fallback_widget is None:
            return ""
        return fallback_widget.textCursor().selectedText().replace("\u2029", "\n")
