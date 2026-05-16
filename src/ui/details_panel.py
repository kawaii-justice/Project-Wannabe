from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.idea_processor import IDEA_ITEM_ORDER, IDEA_ITEM_ORDER_JA
from src.core.settings import DEFAULT_SETTINGS, load_settings
from src.ui.widgets import CollapsibleSection, TagWidget


class DetailsPanel(QWidget):
    details_changed = Signal()
    transfer_requested = Signal(str)
    idea_item_changed = Signal()
    thinking_prefill_enabled_changed = Signal(bool)
    thinking_prefill_transfer_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        details_main_layout = QVBoxLayout(self)
        details_main_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")
        details_main_layout.addWidget(scroll_area)

        scroll_content_widget = QWidget()
        scroll_area.setWidget(scroll_content_widget)
        details_layout = QVBoxLayout(scroll_content_widget)
        details_layout.setSpacing(10)

        self._create_idea_controls(details_layout)
        self._create_rating_section(details_layout, scroll_content_widget)
        self._create_story_detail_sections(details_layout)
        self._create_dialogue_section(details_layout)
        self._create_thinking_prefill_section(details_layout)
        self._connect_change_signals()

        details_layout.addStretch()

    def get_project_details(self) -> dict[str, Any]:
        return {
            "title": self.get_title(),
            "keywords": self.keywords_widget.get_tags(),
            "genres": self.genre_widget.get_tags(),
            "synopsis": self.synopsis_edit.toPlainText(),
            "setting": self.setting_edit.toPlainText(),
            "plot": self.plot_edit.toPlainText(),
            "dialogue_level": self.dialogue_level_combo.currentText(),
            "assistant_thinking_prefill_enabled": self.assistant_thinking_prefill_checkbox.isChecked(),
            "assistant_thinking_prefill": self.assistant_thinking_prefill_edit.toPlainText(),
            "rating": self.rating_combo_details.currentData(),
        }

    def apply_project_details(self, details: dict[str, Any]):
        details = details or {}
        self.title_edit.setText(self._as_text(details.get("title")))
        self.keywords_widget.set_tags(self._as_tag_list(details.get("keywords")))
        self.genre_widget.set_tags(self._as_tag_list(details.get("genres")))
        self.synopsis_edit.setPlainText(self._as_text(details.get("synopsis")))
        self.setting_edit.setPlainText(self._as_text(details.get("setting")))
        self.plot_edit.setPlainText(self._as_text(details.get("plot")))
        self.assistant_thinking_prefill_checkbox.setChecked(
            bool(details.get("assistant_thinking_prefill_enabled", False))
        )
        self.assistant_thinking_prefill_edit.setPlainText(
            self._as_text(details.get("assistant_thinking_prefill"))
        )
        self.set_dialogue_level(self._as_text(details.get("dialogue_level")) or "指定なし")
        self.set_rating(self._as_text(details.get("rating")) or "general")

    def get_generation_data(self) -> dict[str, Any]:
        details = self.get_project_details()
        metadata = {
            "title": details["title"],
            "keywords": details["keywords"],
            "genres": details["genres"],
            "synopsis": details["synopsis"],
            "setting": details["setting"],
            "plot": details["plot"],
        }
        if details["dialogue_level"] != "指定なし":
            metadata["dialogue_level"] = details["dialogue_level"]

        return {
            "metadata": metadata,
            "rating": details["rating"],
            "assistant_thinking_prefill_enabled": details["assistant_thinking_prefill_enabled"],
            "assistant_thinking_prefill": details["assistant_thinking_prefill"],
        }

    def apply_metadata_value(self, metadata_key: str, value: Any):
        if metadata_key == "title":
            self.title_edit.setText(self._as_text(value))
        elif metadata_key == "keywords":
            self.keywords_widget.set_tags(self._as_tag_list(value))
        elif metadata_key == "genres":
            self.genre_widget.set_tags(self._as_tag_list(value))
        elif metadata_key == "synopsis":
            self.synopsis_edit.setPlainText(self._as_text(value))
        elif metadata_key == "setting":
            self.setting_edit.setPlainText(self._as_text(value))
        elif metadata_key == "plot":
            self.plot_edit.setPlainText(self._as_text(value))
        else:
            raise KeyError(metadata_key)

    def get_title(self) -> str:
        return self.title_edit.text()

    def set_rating(self, rating: str):
        rating_index = self.rating_combo_details.findData(rating)
        if rating_index == -1:
            rating_index = self.rating_combo_details.findData("general")
        if rating_index != -1:
            self.rating_combo_details.setCurrentIndex(rating_index)

    def set_dialogue_level(self, level: str):
        if self.dialogue_level_combo.findText(level) == -1:
            level = "指定なし"
        self.dialogue_level_combo.setCurrentText(level)

    def set_thinking_prefill_available(self, enabled: bool):
        if not enabled and self.assistant_thinking_prefill_checkbox.isChecked():
            self.assistant_thinking_prefill_checkbox.setChecked(False)
        self.assistant_thinking_prefill_checkbox.setEnabled(enabled)
        self.assistant_thinking_prefill_edit.setEnabled(enabled)
        self.assistant_thinking_prefill_transfer_button.setEnabled(enabled)
        tooltip = (
            "思考モードが有効な時だけ、ここに入力した思考をassistant prefillとして固定します。"
            if enabled
            else "思考モードを有効にすると使用できます。"
        )
        self.assistant_thinking_prefill_checkbox.setToolTip(tooltip)
        self.assistant_thinking_prefill_edit.setToolTip(tooltip)

    def get_active_thinking_prefill_text(self, thinking_enabled: bool) -> str:
        if not thinking_enabled or not self.assistant_thinking_prefill_checkbox.isChecked():
            return ""
        return self.assistant_thinking_prefill_edit.toPlainText().strip()

    def set_thinking_prefill_text(self, text: str, enabled: bool = True):
        self.assistant_thinking_prefill_edit.setPlainText(text or "")
        self.assistant_thinking_prefill_checkbox.setChecked(enabled)

    def set_idea_controls_visible(self, visible: bool):
        self.idea_controls_widget.setVisible(visible)

    def get_selected_idea_item_key(self) -> str:
        selected_item_index = self.idea_item_combo.currentIndex()
        return self.idea_item_combo.itemData(selected_item_index)

    def get_selected_idea_item_text(self) -> str:
        return self.idea_item_combo.currentText()

    def is_idea_fast_mode_enabled(self) -> bool:
        return self.idea_fast_mode_check.isChecked()

    def set_idea_fast_mode_available(self, enabled: bool):
        self.idea_fast_mode_check.setEnabled(enabled)
        if not enabled:
            self.idea_fast_mode_check.setChecked(False)

    def get_plain_text_edits_for_highlighting(self) -> tuple[QPlainTextEdit, ...]:
        return (
            self.synopsis_edit,
            self.setting_edit,
            self.plot_edit,
            self.assistant_thinking_prefill_edit,
        )

    def get_search_targets(self) -> dict[str, QWidget]:
        return {
            "title": self.title_edit,
            "synopsis": self.synopsis_edit,
            "setting": self.setting_edit,
            "plot": self.plot_edit,
            "assistant_thinking_prefill": self.assistant_thinking_prefill_edit,
        }

    def get_font_targets(self) -> tuple[QWidget, ...]:
        return (
            self.title_edit,
            self.keywords_widget,
            self.genre_widget,
            self.synopsis_edit,
            self.setting_edit,
            self.plot_edit,
            self.assistant_thinking_prefill_edit,
        )

    def _connect_change_signals(self):
        self.title_edit.textChanged.connect(self._emit_details_changed)
        self.keywords_widget.tagsChanged.connect(self._emit_details_changed)
        self.genre_widget.tagsChanged.connect(self._emit_details_changed)
        self.synopsis_edit.textChanged.connect(self._emit_details_changed)
        self.setting_edit.textChanged.connect(self._emit_details_changed)
        self.plot_edit.textChanged.connect(self._emit_details_changed)
        self.rating_combo_details.currentIndexChanged.connect(self._emit_details_changed)
        self.dialogue_level_combo.currentIndexChanged.connect(self._emit_details_changed)
        self.assistant_thinking_prefill_checkbox.toggled.connect(
            self._on_thinking_prefill_enabled_changed
        )
        self.assistant_thinking_prefill_edit.textChanged.connect(self._emit_details_changed)

    def _emit_details_changed(self, *args):
        self.details_changed.emit()

    def _on_thinking_prefill_enabled_changed(self, checked: bool):
        self.details_changed.emit()
        self.thinking_prefill_enabled_changed.emit(checked)

    def _as_text(self, value: Any) -> str:
        return "" if value is None else str(value)

    def _as_tag_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            parts = value.replace("、", ",").split(",")
            return [part.strip() for part in parts if part.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    def _create_idea_controls(self, details_layout: QVBoxLayout):
        self.idea_controls_widget = QWidget()
        idea_controls_layout = QVBoxLayout(self.idea_controls_widget)
        idea_controls_layout.setContentsMargins(5, 5, 5, 5)
        idea_controls_layout.setSpacing(5)

        idea_item_layout = QHBoxLayout()
        idea_item_label = QLabel("生成項目:")
        self.idea_item_combo = QComboBox()
        self.idea_item_combo.addItem("全部", "all")
        for item_key, item_ja in zip(IDEA_ITEM_ORDER, IDEA_ITEM_ORDER_JA):
            self.idea_item_combo.addItem(item_ja, item_key)
        idea_item_layout.addWidget(idea_item_label)
        idea_item_layout.addWidget(self.idea_item_combo)
        idea_controls_layout.addLayout(idea_item_layout)

        self.idea_fast_mode_check = QCheckBox("高速な手法（実験的）")
        idea_controls_layout.addWidget(self.idea_fast_mode_check)

        details_layout.addWidget(self.idea_controls_widget)
        self.idea_controls_widget.hide()
        self.idea_item_combo.currentIndexChanged.connect(lambda _index: self.idea_item_changed.emit())

    def _create_rating_section(self, details_layout: QVBoxLayout, parent: QWidget):
        rating_section = CollapsibleSection("レーティング (生成時)", parent=parent)
        rating_layout = QHBoxLayout()
        rating_label = QLabel("レーティング:")
        self.rating_combo_details = QComboBox()
        self.rating_combo_details.addItem("General (全年齢)", "general")
        self.rating_combo_details.addItem("R-18", "r18")
        rating_layout.addWidget(rating_label)
        rating_layout.addWidget(self.rating_combo_details)
        rating_layout.addStretch()
        rating_section.content_layout.addLayout(rating_layout)
        details_layout.addWidget(rating_section)

        initial_settings = load_settings()
        initial_rating = initial_settings.get("default_rating", DEFAULT_SETTINGS["default_rating"])
        initial_rating_index = self.rating_combo_details.findData(initial_rating)
        if initial_rating_index != -1:
            self.rating_combo_details.setCurrentIndex(initial_rating_index)

    def _create_story_detail_sections(self, details_layout: QVBoxLayout):
        title_section = CollapsibleSection("タイトル")
        title_layout = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_transfer_button = self._create_transfer_button("title")
        title_layout.addWidget(self.title_edit)
        title_layout.addWidget(self.title_transfer_button)
        title_section.content_layout.addLayout(title_layout)
        details_layout.addWidget(title_section)

        keywords_section = CollapsibleSection("キーワード")
        self.keywords_widget = TagWidget()
        self._wire_tag_transfer(self.keywords_widget, "keywords")
        keywords_section.addWidget(self.keywords_widget)
        details_layout.addWidget(keywords_section)

        genre_section = CollapsibleSection("ジャンル")
        self.genre_widget = TagWidget()
        self._wire_tag_transfer(self.genre_widget, "genres")
        genre_section.addWidget(self.genre_widget)
        details_layout.addWidget(genre_section)

        synopsis_section = CollapsibleSection("あらすじ")
        synopsis_layout = QHBoxLayout()
        self.synopsis_edit = QPlainTextEdit()
        self.synopsis_edit.setPlaceholderText("小説のあらすじを入力...")
        self.synopsis_transfer_button = self._create_transfer_button("synopsis")
        synopsis_layout.addWidget(self.synopsis_edit)
        synopsis_layout.addWidget(self.synopsis_transfer_button, 0, Qt.AlignTop)
        synopsis_section.content_layout.addLayout(synopsis_layout)
        details_layout.addWidget(synopsis_section)

        setting_section = CollapsibleSection("設定")
        setting_layout = QHBoxLayout()
        self.setting_edit = QPlainTextEdit()
        self.setting_edit.setPlaceholderText("世界観、キャラクター設定などを入力...")
        self.setting_transfer_button = self._create_transfer_button("setting")
        setting_layout.addWidget(self.setting_edit)
        setting_layout.addWidget(self.setting_transfer_button, 0, Qt.AlignTop)
        setting_section.content_layout.addLayout(setting_layout)
        details_layout.addWidget(setting_section)

        plot_section = CollapsibleSection("プロット")
        plot_layout = QHBoxLayout()
        self.plot_edit = QPlainTextEdit()
        self.plot_edit.setPlaceholderText("物語の展開、構成などを入力...")
        self.plot_transfer_button = self._create_transfer_button("plot")
        plot_layout.addWidget(self.plot_edit)
        plot_layout.addWidget(self.plot_transfer_button, 0, Qt.AlignTop)
        plot_section.content_layout.addLayout(plot_layout)
        details_layout.addWidget(plot_section)

    def _create_dialogue_section(self, details_layout: QVBoxLayout):
        dialogue_section = CollapsibleSection("セリフ量 (生成時)")
        dialogue_layout = QHBoxLayout()
        dialogue_label = QLabel("セリフ量:")
        self.dialogue_level_combo = QComboBox()
        self.dialogue_level_combo.addItems([
            "指定なし", "少ない", "やや少ない", "普通", "やや多い", "多い"
        ])
        dialogue_layout.addWidget(dialogue_label)
        dialogue_layout.addWidget(self.dialogue_level_combo)
        dialogue_layout.addStretch()
        dialogue_section.content_layout.addLayout(dialogue_layout)
        details_layout.addWidget(dialogue_section)

    def _create_thinking_prefill_section(self, details_layout: QVBoxLayout):
        assistant_thinking_section = CollapsibleSection("思考prefill (生成時)")
        assistant_thinking_controls = QHBoxLayout()
        self.assistant_thinking_prefill_checkbox = QCheckBox("思考を固定（思考有効時のみ）")
        self.assistant_thinking_prefill_checkbox.setToolTip(
            "思考モードが有効な時だけ、ここに入力した思考をassistant prefillとして固定します。"
        )
        self.assistant_thinking_prefill_transfer_button = QPushButton("← 選択思考を転記")
        self.assistant_thinking_prefill_transfer_button.setFocusPolicy(Qt.NoFocus)
        self.assistant_thinking_prefill_transfer_button.clicked.connect(
            self.thinking_prefill_transfer_requested.emit
        )
        assistant_thinking_controls.addWidget(self.assistant_thinking_prefill_checkbox)
        assistant_thinking_controls.addWidget(self.assistant_thinking_prefill_transfer_button)
        assistant_thinking_controls.addStretch()

        self.assistant_thinking_prefill_edit = QPlainTextEdit()
        self.assistant_thinking_prefill_edit.setPlaceholderText(
            "出力欄の思考など、あらかじめ流し込みたい思考を入力..."
        )
        self.assistant_thinking_prefill_edit.setMinimumHeight(90)
        assistant_thinking_section.content_layout.addLayout(assistant_thinking_controls)
        assistant_thinking_section.addWidget(self.assistant_thinking_prefill_edit)
        details_layout.addWidget(assistant_thinking_section)

    def _create_transfer_button(self, metadata_key: str) -> QPushButton:
        button = QPushButton("← 転記")
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda _checked=False, key=metadata_key: self.transfer_requested.emit(key))
        return button

    def _wire_tag_transfer(self, tag_widget: TagWidget, metadata_key: str):
        tag_widget.transfer_button.setFocusPolicy(Qt.NoFocus)
        tag_widget.transfer_button.clicked.connect(
            lambda _checked=False, key=metadata_key: self.transfer_requested.emit(key)
        )
