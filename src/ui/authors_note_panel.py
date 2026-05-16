from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QPlainTextEdit, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class AuthorsNotePanel(QWidget):
    toggle_requested = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._expanded = False
        self._last_expanded_height = 220

        self.setObjectName("authorsNotePanel")
        panel_layout = QVBoxLayout(self)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("authorsNoteCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(4)

        self.toggle_button = QToolButton()
        self.toggle_button.setObjectName("authorsNoteToggle")
        self.toggle_button.setText("次の展開の指示")
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.UpArrow)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setFocusPolicy(Qt.NoFocus)
        self.toggle_button.setMinimumHeight(26)
        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle_button.clicked.connect(lambda checked: self.toggle_requested.emit(checked))
        card_layout.addWidget(self.toggle_button)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setObjectName("authorsNoteEditor")
        self.text_edit.setPlaceholderText(
            "この先1000文字程度の展開・要素を記述\n"
            "例:\n"
            "主人公のエルフの少女が、森の中で迷子のドラゴンと出会うシーン。\n"
            "驚きと少しの警戒心、そして好奇心が入り混じった描写を。\n\n"
            "または単語の羅列も可能です。例:\n"
            "主人公エルフ\n"
            "迷子ドラゴン登場"
        )
        self.text_edit.setMinimumHeight(44)
        self.text_edit.textChanged.connect(self._update_toggle_state)
        card_layout.addWidget(self.text_edit)

        panel_layout.addWidget(self.card)
        self.set_expanded(False, remember_height=False)

    @property
    def expanded(self) -> bool:
        return self._expanded

    def collapsed_height(self) -> int:
        return max(self.toggle_button.sizeHint().height(), self.toggle_button.minimumHeight()) + 2

    def expanded_min_height(self) -> int:
        card_vertical_margin = 8
        card_spacing = 4
        border_allowance = 4
        return (
            self.toggle_button.sizeHint().height()
            + self.text_edit.minimumHeight()
            + card_spacing
            + card_vertical_margin
            + border_allowance
        )

    def set_expanded(
        self,
        expanded: bool,
        *,
        remember_height: bool = True,
        current_height: int | None = None,
    ) -> int:
        expanded = bool(expanded)
        collapsed_height = self.collapsed_height()
        if remember_height and not expanded and current_height is not None and current_height > collapsed_height + 8:
            self._last_expanded_height = current_height

        self._expanded = expanded
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.UpArrow)
        self.toggle_button.blockSignals(False)
        self._update_toggle_state()
        self.text_edit.setVisible(expanded)

        if expanded:
            min_height = self.expanded_min_height()
            target_height = max(min_height, self._last_expanded_height)
            self.setMinimumHeight(min_height)
            self.setMaximumHeight(16777215)
        else:
            target_height = collapsed_height
            self.setMinimumHeight(collapsed_height)
            self.setMaximumHeight(collapsed_height)

        self.apply_style()
        return target_height

    def _update_toggle_state(self):
        has_note = bool(self.text_edit.toPlainText().strip())
        if has_note and not self._expanded:
            self.toggle_button.setText("次の展開の指示（適用中）")
            self.toggle_button.setToolTip("次の展開の指示が入力されています。クリックして編集します。")
        else:
            self.toggle_button.setText("次の展開の指示")
            self.toggle_button.setToolTip("次の展開の指示を開閉します。")
        self.apply_style()

    def apply_style(self):
        palette = QApplication.palette()
        window = palette.color(QPalette.Window)
        base = palette.color(QPalette.Base)
        button = palette.color(QPalette.Button)
        text = palette.color(QPalette.Text)
        mid = palette.color(QPalette.Mid)
        highlight = palette.color(QPalette.Highlight)
        highlighted_text = palette.color(QPalette.HighlightedText)
        dark_ui = window.lightness() < 128
        has_note = bool(self.text_edit.toPlainText().strip())

        def tune(color: QColor, amount: int, lighter: bool) -> str:
            return (color.lighter(amount) if lighter else color.darker(amount)).name()

        if self._expanded:
            panel_bg = tune(window, 108 if dark_ui else 103, lighter=dark_ui)
            border = tune(highlight, 135 if dark_ui else 115, lighter=dark_ui)
            editor_bg = tune(base, 106 if dark_ui else 101, lighter=dark_ui)
            toggle_bg = highlight.name()
            toggle_text = highlighted_text.name()
            card_border = f"1px solid {border}"
            editor_border = "none"
            toggle_border = "none"
        else:
            panel_bg = "transparent"
            border = tune(
                highlight if has_note else mid,
                135 if dark_ui else 110,
                lighter=dark_ui if has_note else not dark_ui,
            )
            header_bg = tune(
                highlight if has_note else button,
                132 if dark_ui else 122,
                lighter=dark_ui if has_note else not dark_ui,
            )
            editor_bg = base.name()
            toggle_bg = header_bg
            toggle_text = highlighted_text.name() if has_note else text.name()
            card_border = "none"
            editor_border = f"1px solid {border}"
            toggle_border = f"1px solid {border}"

        self.layout().setContentsMargins(0, 0, 0, 0)
        self.card.layout().setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet(
            "QWidget#authorsNotePanel {"
            " background-color: transparent;"
            " border: none;"
            "}"
            "QFrame#authorsNoteCard {"
            f" background-color: {panel_bg};"
            f" border: {card_border};"
            " border-radius: 6px;"
            " padding: 0px;"
            "}"
            "QToolButton#authorsNoteToggle {"
            f" background-color: {toggle_bg};"
            f" color: {toggle_text};"
            f" border: {toggle_border};"
            " border-radius: 4px;"
            " padding: 2px 10px;"
            " font-weight: 600;"
            " text-align: left;"
            "}"
            "QPlainTextEdit#authorsNoteEditor {"
            f" background-color: {editor_bg};"
            f" color: {text.name()};"
            f" border: {editor_border};"
            " border-radius: 5px;"
            " padding: 8px;"
            " selection-background-color: palette(highlight);"
            "}"
        )
