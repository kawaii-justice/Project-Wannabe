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
    transfer_requested = Signal(str)
    idea_item_changed = Signal()
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

        details_layout.addStretch()

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
