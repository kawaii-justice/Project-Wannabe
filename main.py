import sys
import asyncio
import qasync # Import qasync
import re # Import regex module
from PySide6.QtWidgets import (QApplication, QMainWindow, QMenuBar, QStatusBar,
                               QSplitter, QTextEdit, QWidget, QVBoxLayout, QHBoxLayout,
                               QTabWidget, QScrollArea, QLineEdit, QPushButton, QMessageBox,
                               QPlainTextEdit, QToolBar, QDialog, QLineEdit, QLabel, QComboBox, # Add QLabel, QComboBox
                               QCheckBox, QPlainTextEdit, QSizePolicy) # Ensure QPlainTextEdit is imported, Add QCheckBox, QSizePolicy
from PySide6.QtCore import Qt, Slot, QTimer, QEvent # Add QEvent
from PySide6.QtGui import QTextCursor, QAction, QActionGroup, QFont, QKeyEvent # Add QKeyEvent
from typing import Dict, Optional, List # Add Optional and List here

# Correctly import custom widgets and other modules
from src.ui.widgets import CollapsibleSection, TagWidget
from src.ui.dialogs import KoboldConfigDialog, GenerationParamsDialog
from src.core.kobold_client import KoboldClient, KoboldClientError
from src.core.prompt_builder import build_prompt, build_prompt_with_compression
from src.core.dynamic_prompts import evaluate_dynamic_prompt
from src.core.settings import load_settings, DEFAULT_SETTINGS
from src.ui.menu_handler import MenuHandler
# Import IdeaProcessor and constants
from src.core.idea_processor import IdeaProcessor, IDEA_ITEM_ORDER, IDEA_ITEM_ORDER_JA, METADATA_MAP
from src.core.context_utils import count_tokens, get_available_context, get_true_max_context_length # Import for token counting

# Import AutocompleteManager
from src.core.autocomplete_manager import AutocompleteManager

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Project Wannabe") # "(仮称)" を削除
        self.setGeometry(100, 100, 1200, 800)

        self.kobold_client = KoboldClient()
        # Generation status: "idle", "single_running", "infinite_running"
        self.generation_status = "idle"
        self.generation_task = None # Holds the asyncio task for generation
        self.output_block_counter = 1
        self.current_mode = "generate" # Initial mode: "generate" or "idea"
        self.infinite_generation_prompt = "" # Store prompt for infinite loop
        self.idea_item_key_map = {name_ja: key for key, name_ja in METADATA_MAP.items() if key in IDEA_ITEM_ORDER} # Map JA name to key

        # Instantiate MenuHandler
        self.menu_handler = MenuHandler(self)

        # Placeholders for IDEA UI elements
        self.idea_controls_widget = None
        self.idea_item_combo = None
        self.idea_fast_mode_check = None
        self.infinite_warning_shown = False # Flag for infinite gen warning

        # Token tracking variables
        self.main_text_tokens = 0
        self.prompt_tokens = 0
        self.available_context_tokens = 0
        self.token_update_timer = QTimer()
        self.token_update_timer.timeout.connect(self._on_token_timer_timeout)
        self.token_update_timer.start(500)  # Update every 500ms

        # Create UI elements
        self._create_toolbar() # Create toolbar first
        self._create_status_bar()
        self._create_central_widget() # Create central widget before menu bar needs it
        self._create_menu_bar() # Create menu bar using the handler

        # AutocompleteManagerの初期化（main_text_edit作成後）
        self.autocomplete_manager = AutocompleteManager(self.main_text_edit, self.kobold_client)
        
        # イベントフィルターをインストール
        self.main_text_edit.installEventFilter(self)
        
        # 初期状態のショートカット表示を更新
        self._update_shortcut_display()

        # Apply initial theme and font from settings via MenuHandler
        # These might be called within MenuHandler's creation logic already
        # self.menu_handler._apply_initial_font() # Ensure initial font is applied
        # self.menu_handler._apply_theme(load_settings().get("theme", "light")) # Ensure initial theme

    def _create_menu_bar(self):
        """Creates the menu bar using MenuHandler."""
        self.setMenuBar(self.menu_handler.create_menu_bar())

    def _create_toolbar(self):
        """Creates the main toolbar for mode switching."""
        toolbar = QToolBar("モード選択")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)

        self.gen_mode_action = QAction("小説生成", self)
        self.gen_mode_action.setCheckable(True)
        self.gen_mode_action.setChecked(True)
        self.gen_mode_action.triggered.connect(self._set_mode_generate)
        toolbar.addAction(self.gen_mode_action)
        mode_group.addAction(self.gen_mode_action)

        self.idea_mode_action = QAction("アイデア出し", self)
        self.idea_mode_action.setCheckable(True)
        self.idea_mode_action.triggered.connect(self._set_mode_idea)
        toolbar.addAction(self.idea_mode_action)
        mode_group.addAction(self.idea_mode_action)

        toolbar.addSeparator()

        # 執筆支援モード（オートコンプリート）チェックボックス
        self.autocomplete_checkbox = QCheckBox("リアルタイムで続きを提案（ベータ）")
        self.autocomplete_checkbox.setChecked(False)  # 初期状態はOFF
        self.autocomplete_checkbox.setFocusPolicy(Qt.NoFocus)  # フォーカスを無効化してショートカット暴発を防止
        self.autocomplete_checkbox.toggled.connect(self._toggle_autocomplete_mode)
        toolbar.addWidget(self.autocomplete_checkbox)
        
        # スペーサーを追加して右端にショートカット説明を配置
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        
        # ショートカットキー説明ラベル
        self.shortcut_label = QLabel("単発生成: Ctrl+G | 無限生成: F5")
        toolbar.addWidget(self.shortcut_label)

    def _create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("準備完了") # Changed to Japanese
        
        # Create permanent widget for token display
        self.token_label = QLabel("本文文字数0文字(0トークン) | 全プロンプト: 0 / 0トークン")
        self.status_bar.addPermanentWidget(self.token_label)

    def _create_central_widget(self):
        central_splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(central_splitter)

        left_widget = QWidget()
        left_main_layout = QVBoxLayout(left_widget)
        left_main_layout.setContentsMargins(0,0,0,0)
        left_main_layout.setSpacing(0)
        left_splitter = QSplitter(Qt.Vertical)
        left_main_layout.addWidget(left_splitter)

        main_text_container = QWidget()
        main_text_layout = QVBoxLayout(main_text_container)
        main_text_layout.setContentsMargins(0, 5, 0, 0)
        main_text_layout.setSpacing(5)
        self.main_text_edit = QPlainTextEdit()
        self.main_text_edit.setPlaceholderText("ここに小説本文を入力・編集します...")
        main_text_layout.addWidget(self.main_text_edit)
        left_splitter.addWidget(main_text_container)

        output_container = QWidget()
        output_layout = QVBoxLayout(output_container)
        output_layout.setContentsMargins(0, 5, 0, 0)
        output_layout.setSpacing(5)
        self.output_text_edit = QPlainTextEdit()
        self.output_text_edit.setReadOnly(True)
        self.output_text_edit.setPlaceholderText("LLMからの出力がここに表示されます...")
        output_layout.addWidget(self.output_text_edit)
        output_button_layout = QHBoxLayout()
        output_clear_button = QPushButton("[ 出力物クリア ]")
        output_to_main_button = QPushButton("[ 選択部分を本文へ転記 ]")
        output_to_memo_button = QPushButton("[ 選択部分をメモへ転記 ]")
        output_clear_button.clicked.connect(self._clear_output_edit)
        output_to_main_button.clicked.connect(self._transfer_output_to_main)
        output_to_memo_button.clicked.connect(self._transfer_output_to_memo)
        output_button_layout.addWidget(output_clear_button)
        output_button_layout.addWidget(output_to_main_button)
        output_button_layout.addWidget(output_to_memo_button)
        output_button_layout.addStretch()
        output_layout.addLayout(output_button_layout)
        left_splitter.addWidget(output_container)

        self.right_tab_widget = QTabWidget()
        self._create_details_tab()
        self._create_memo_tab()
        self.right_tab_widget.addTab(self.details_tab_widget, "詳細情報")
        self.right_tab_widget.addTab(self.memo_tab_widget, "メモ")

        central_splitter.addWidget(left_widget)
        central_splitter.addWidget(self.right_tab_widget)
        central_splitter.setSizes([700, 500])
        left_splitter.setSizes([600, 200])

    def _create_details_tab(self):
        self.details_tab_widget = QWidget()
        details_main_layout = QVBoxLayout(self.details_tab_widget)
        details_main_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")
        details_main_layout.addWidget(scroll_area)
        scroll_content_widget = QWidget()
        scroll_area.setWidget(scroll_content_widget)
        details_layout = QVBoxLayout(scroll_content_widget)
        details_layout.setSpacing(10) # Increase spacing slightly

        # --- IDEA Task Controls (Initially Hidden) ---
        self.idea_controls_widget = QWidget()
        idea_controls_layout = QVBoxLayout(self.idea_controls_widget)
        idea_controls_layout.setContentsMargins(5, 5, 5, 5)
        idea_controls_layout.setSpacing(5)

        idea_item_layout = QHBoxLayout()
        idea_item_label = QLabel("生成項目:")
        self.idea_item_combo = QComboBox()
        self.idea_item_combo.addItem("全部", "all") # Add "all" option with internal key
        for i, item_ja in enumerate(IDEA_ITEM_ORDER_JA):
            item_key = IDEA_ITEM_ORDER[i]
            self.idea_item_combo.addItem(item_ja, item_key) # Store internal key as data
        idea_item_layout.addWidget(idea_item_label)
        idea_item_layout.addWidget(self.idea_item_combo)
        idea_controls_layout.addLayout(idea_item_layout)

        self.idea_fast_mode_check = QCheckBox("高速な手法（実験的）")
        idea_controls_layout.addWidget(self.idea_fast_mode_check)

        # Add a separator or some visual distinction if desired
        # separator = QFrame()
        # separator.setFrameShape(QFrame.HLine)
        # separator.setFrameShadow(QFrame.Sunken)
        # idea_controls_layout.addWidget(separator)

        details_layout.addWidget(self.idea_controls_widget)
        self.idea_controls_widget.hide() # Hide initially
        # Connect signal after creation
        self.idea_item_combo.currentIndexChanged.connect(self._update_idea_fast_mode_state)
        # --- End IDEA Task Controls ---


        # --- Rating Selection ---
        # Make rating section collapsible as well
        rating_section = CollapsibleSection("レーティング (生成時)", parent=scroll_content_widget)
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
        # Load initial rating from settings (ensure this happens after combo box creation)
        initial_settings = load_settings()
        initial_rating = initial_settings.get("default_rating", DEFAULT_SETTINGS["default_rating"])
        initial_rating_index = self.rating_combo_details.findData(initial_rating)
        if initial_rating_index != -1:
            self.rating_combo_details.setCurrentIndex(initial_rating_index)
        # --- End Rating Selection ---

        # Title
        title_section = CollapsibleSection("タイトル")
        title_layout = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_transfer_button = QPushButton("← 転記")
        self.title_transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("title"))
        title_layout.addWidget(self.title_edit)
        title_layout.addWidget(self.title_transfer_button)
        title_section.content_layout.addLayout(title_layout)
        details_layout.addWidget(title_section)

        # Keywords
        keywords_section = CollapsibleSection("キーワード")
        self.keywords_widget = TagWidget()
        self.keywords_widget.transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("keywords"))
        keywords_section.addWidget(self.keywords_widget)
        details_layout.addWidget(keywords_section)

        # Genre
        genre_section = CollapsibleSection("ジャンル")
        self.genre_widget = TagWidget()
        self.genre_widget.transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("genres"))
        genre_section.addWidget(self.genre_widget)
        details_layout.addWidget(genre_section)

        # Synopsis
        synopsis_section = CollapsibleSection("あらすじ")
        synopsis_layout = QHBoxLayout()
        self.synopsis_edit = QPlainTextEdit()
        self.synopsis_edit.setPlaceholderText("小説のあらすじを入力...")
        self.synopsis_transfer_button = QPushButton("← 転記")
        self.synopsis_transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("synopsis"))
        synopsis_layout.addWidget(self.synopsis_edit)
        synopsis_layout.addWidget(self.synopsis_transfer_button, 0, Qt.AlignTop)
        synopsis_section.content_layout.addLayout(synopsis_layout)
        details_layout.addWidget(synopsis_section)

        # Setting
        setting_section = CollapsibleSection("設定")
        setting_layout = QHBoxLayout()
        self.setting_edit = QPlainTextEdit()
        self.setting_edit.setPlaceholderText("世界観、キャラクター設定などを入力...")
        self.setting_transfer_button = QPushButton("← 転記")
        self.setting_transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("setting"))
        setting_layout.addWidget(self.setting_edit)
        setting_layout.addWidget(self.setting_transfer_button, 0, Qt.AlignTop)
        setting_section.content_layout.addLayout(setting_layout)
        details_layout.addWidget(setting_section)

        # Plot
        plot_section = CollapsibleSection("プロット")
        plot_layout = QHBoxLayout()
        self.plot_edit = QPlainTextEdit()
        self.plot_edit.setPlaceholderText("物語の展開、構成などを入力...")
        self.plot_transfer_button = QPushButton("← 転記")
        self.plot_transfer_button.clicked.connect(lambda: self._transfer_idea_to_details("plot"))
        plot_layout.addWidget(self.plot_edit)
        plot_layout.addWidget(self.plot_transfer_button, 0, Qt.AlignTop)
        plot_section.content_layout.addLayout(plot_layout)
        details_layout.addWidget(plot_section)

        # Author's Note
        authors_note_section = CollapsibleSection("次の展開についてのメモ")
        authors_note_layout = QHBoxLayout()
        self.authors_note_edit = QPlainTextEdit()
        self.authors_note_edit.setPlaceholderText("この先1000文字程度の展開・要素を記述\n例：\n主人公のエルフの少女が、森の中で迷子のドラゴンと出会うシーン。\n驚きと少しの警戒心、そして好奇心が入り混じった描写を。\n\nまたは単語の羅列も可能です。例：\n主人公エルフ\n迷子ドラゴン登場")
        # Optionally set a fixed height or leave it default
        # self.authors_note_edit.setFixedHeight(100)
        authors_note_layout.addWidget(self.authors_note_edit)
        # No transfer button needed for author's note typically
        authors_note_section.content_layout.addLayout(authors_note_layout)
        details_layout.addWidget(authors_note_section)

        # Dialogue Level
        dialogue_section = CollapsibleSection("セリフ量 (生成時)") # Clarify title
        dialogue_layout = QHBoxLayout()
        dialogue_label = QLabel("セリフ量:")
        self.dialogue_level_combo = QComboBox()
        self.dialogue_level_combo.addItems([
            "指定なし", "少ない", "やや少ない", "普通", "やや多い", "多い"
        ])
        dialogue_layout.addWidget(dialogue_label)
        dialogue_layout.addWidget(self.dialogue_level_combo)
        dialogue_layout.addStretch() # Add stretch to push combo box to the left
        dialogue_section.content_layout.addLayout(dialogue_layout)
        details_layout.addWidget(dialogue_section)

        details_layout.addStretch()

    def _create_memo_tab(self):
        self.memo_tab_widget = QWidget()
        memo_layout = QVBoxLayout(self.memo_tab_widget)
        self.memo_edit = QPlainTextEdit()
        self.memo_edit.setPlaceholderText("自由にメモを記入できます...")
        memo_clear_button = QPushButton("メモクリア")
        memo_clear_button.clicked.connect(self._clear_memo_edit)
        memo_layout.addWidget(self.memo_edit)
        memo_layout.addWidget(memo_clear_button, 0, Qt.AlignRight)

    def _clear_memo_edit(self):
        self.memo_edit.clear()

    def _open_kobold_config_dialog(self):
        dialog = KoboldConfigDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.status_bar.showMessage("KoboldCpp 設定が更新されました。", 3000)
            self.kobold_client.reload_settings()
            self.autocomplete_manager.reload_settings()  # オートコンプリート設定も再読み込み
        else:
            self.status_bar.showMessage("KoboldCpp 設定の変更はキャンセルされました。", 3000)

    def _open_gen_params_dialog(self):
        dialog = GenerationParamsDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.status_bar.showMessage("生成パラメータが更新されました。", 3000)
            self.kobold_client.reload_settings()
            self.autocomplete_manager.reload_settings()  # オートコンプリート設定も再読み込み
        else:
            self.status_bar.showMessage("生成パラメータの変更はキャンセルされました。", 3000)

    # --- Generation Control Slots ---
    @Slot()
    def _trigger_single_generation(self):
        """Starts a single generation task, or stops it if already running."""
        # 生成開始前にゴーストテキストをクリア
        if hasattr(self, 'autocomplete_manager') and self.autocomplete_manager:
            self.autocomplete_manager.clear_ghost_text()
        
        if self.generation_status == "single_running":
            # If single generation is running, stop it.
            self._stop_current_generation()
            return
        elif self.generation_status == "infinite_running":
            # If infinite generation is running, show warning and do nothing.
            QMessageBox.warning(self, "生成中", "現在、無限生成が実行中です。停止してから単発生成を開始してください。")
            return
        elif self.generation_status != "idle":
            # Handle unexpected status (should ideally not happen)
            QMessageBox.warning(self, "不明な状態", f"予期せぬ生成ステータスです: {self.generation_status}")
            return

        # Only proceed if status is idle
        # --- IDEA Mode Logic ---
        if self.current_mode == "idea":
            selected_item_index = self.idea_item_combo.currentIndex()
            selected_item_key = self.idea_item_combo.itemData(selected_item_index) # Get internal key ('all', 'title', etc.)
            fast_mode_enabled = self.idea_fast_mode_check.isChecked()
            ui_inputs = self._get_metadata_from_ui()["metadata"] # Get only metadata part

            processor = IdeaProcessor(ui_inputs)
            stop_sequence = processor.determine_stop_sequence(selected_item_key)
            prompt_suffix = ""
            prereqs_met = True # Assume met unless fast mode check fails

            if fast_mode_enabled:
                prereqs_met, warning_msg = processor.check_fast_mode_prerequisites(selected_item_key)
                if warning_msg:
                    QMessageBox.warning(self, "前提条件に関する警告", warning_msg)
                    # Continue even if prereqs_met is False, as per user request

                # Generate suffix regardless of warning, as we are continuing
                prompt_suffix = processor.generate_prompt_suffix(selected_item_key)

            # Get base prompt (unchanged logic for IDEA mode in build_prompt)
            # Pass the full ui_data including rating and authors_note to build_prompt
            full_ui_data = self._get_metadata_from_ui()
            base_prompt = build_prompt(
                current_mode="idea",
                main_text="", # main_text is not used for IDEA mode
                ui_data=full_ui_data,
                cont_prompt_order="reference_first" # This setting doesn't affect IDEA mode
            )

            final_prompt = base_prompt + prompt_suffix

            # --- Execute Generation based on mode ---
            self.generation_status = "single_running" # Use single_running status for IDEA task
            self._update_ui_for_generation_start() # Update UI (e.g., status bar)

            # Use unified separator format including counter
            separator = f"\n--- アイデア生成 ({self.idea_item_combo.currentText()}) ({self.output_block_counter}) ---\n"
            self._append_to_output(separator)

            # IDEA "all" item or fast mode should stream
            if selected_item_key == "all" or fast_mode_enabled:
                self.generation_task = asyncio.ensure_future(
                    self._run_single_generation(final_prompt, stop_sequence=stop_sequence)
                )
            # else: # Safe Mode (specific item, not fast)
            #     # Safe Mode: Get full output, then filter
            #     self.generation_task = asyncio.ensure_future(
            #         self._run_safe_idea_generation(final_prompt, stop_sequence=stop_sequence, selected_item_key=selected_item_key)
            #     )
            # Simplified: If not 'all' and not 'fast', it must be 'safe'
            else: # Safe Mode (specific item, not fast)
                self.generation_task = asyncio.ensure_future(
                    self._run_safe_idea_generation(final_prompt, stop_sequence=stop_sequence, selected_item_key=selected_item_key)
                )


        # --- Generate Mode Logic (with dynamic compression) ---
        else:  # self.current_mode == "generate"
            self.generation_status = "single_running"
            self._update_ui_for_generation_start()

            # 本文とUIデータ取得
            raw_main_text = self.main_text_edit.toPlainText()
            main_text = evaluate_dynamic_prompt(raw_main_text)
            ui_data = self._get_metadata_from_ui()

            settings = load_settings()
            cont_order = settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])
            compression_mode = settings.get("compression_mode", DEFAULT_SETTINGS.get("compression_mode", "token_dynamic"))

            # モード別 最大出力長
            max_len_generate = settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])

            # KoboldCppベースURL
            base_url = self.kobold_client._get_api_base_url()

            # 動的圧縮付きプロンプト構築
            async def _build_and_run():
                try:
                    # 圧縮開始前にステータス表示
                    QTimer.singleShot(0, lambda: self.status_bar.showMessage("本文圧縮中..."))
                    
                    prompt, total_tokens, is_overflow, original_chars, compressed_chars = await build_prompt_with_compression(
                        base_url=base_url,
                        current_mode=self.current_mode,
                        main_text=main_text,
                        ui_data=ui_data,
                        cont_prompt_order=cont_order,
                        compression_mode=compression_mode,
                        max_length_generate=max_len_generate,
                    )
                    
                    # 圧縮後、元の生成中ステータスに戻す
                    QTimer.singleShot(0, lambda: self.status_bar.showMessage("単発生成中..."))

                    # 圧縮しても収まりきらない場合（コンテキスト長超過）の処理
                    if is_overflow:
                        QMessageBox.critical(
                            self,
                            "コンテキスト長超過により生成できません",
                            (
                                "本文と詳細情報を圧縮しましたが、それでもモデルの最大コンテキスト長を超過しているため生成を実行できません。\n\n"
                                "以下のいずれか、または複数の対応を行ってください。\n"
                                "・詳細情報を推敲して、必要な内容だけを記載する\n"
                                "・KoboldCpp の設定から AI のコンテキスト長を増やす\n"
                                "・最大出力長を減らす\n"
                                "・（非推奨）設定の「最大コンテキスト超過時の処理」で『何もしない』を選択し、このチェックを無視する"
                            ),
                        )
                        self.generation_status = "idle"
                        self._update_ui_for_generation_stop()
                        self.generation_task = None
                        return

                    # 本文圧縮率＋文字数に基づく品質警告
                    if original_chars and compressed_chars is not None and original_chars > 0:
                        ratio = compressed_chars / float(original_chars)
                        min_ratio = settings.get(
                            "warn_short_context_min_ratio",
                            DEFAULT_SETTINGS.get("warn_short_context_min_ratio", 0.5),
                        )
                        min_chars = settings.get(
                            "warn_short_context_min_chars",
                            DEFAULT_SETTINGS.get("warn_short_context_min_chars", 2500),
                        )
                        if ratio < min_ratio and compressed_chars < min_chars:
                            res = QMessageBox.warning(
                                self,
                                "コンテキスト圧縮に関する警告",
                                (
                                    "詳細情報がAIのコンテキスト（メモリ）に対して大きすぎます。そのため本文が大きく圧縮されており、"
                                    "生成品質が低下している可能性があります。このまま続行しますか？\n\n"
                                    "以下の対応を推奨します。\n"
                                    "・詳細情報を推敲して、必要な内容だけを記載する\n"
                                    "・KoboldCppの設定からAIのコンテキスト長を増やす\n"
                                    "・最大出力長を減らす"
                                ),
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No,
                            )
                            if res == QMessageBox.No:
                                self.generation_status = "idle"
                                self._update_ui_for_generation_stop()
                                self.generation_task = None
                                return

                    separator = f"\n--- 生成ブロック {self.output_block_counter} ---\n"
                    self._append_to_output(separator)

                    # 実際の生成実行
                    await self._run_single_generation(prompt, stop_sequence=None)

                except KoboldClientError as e:
                    self._append_to_output(f"\n--- 単発生成エラー: {e} ---\n")
                    self.status_bar.showMessage("単発生成 エラー", 3000)
                except Exception as e:
                    self._append_to_output(f"\n--- 単発生成中に予期せぬエラー: {e} ---\n")
                    self.status_bar.showMessage("単発生成 予期せぬエラー", 3000)

            # 非同期タスクとして実行
            self.generation_task = asyncio.ensure_future(_build_and_run())

    @Slot()
    def _toggle_infinite_generation(self):
        """Starts/stops infinite generation, or stops single generation if running."""
        if self.generation_status == "infinite_running":
            # If infinite is running, stop it.
            self._stop_current_generation()
        elif self.generation_status == "single_running":
            # If single is running, stop it.
            self._stop_current_generation()
            # Ensure the infinite gen button remains unchecked as we just stopped single gen
            self.infinite_gen_action.setChecked(False)
        elif self.generation_status == "idle":
            # If idle, start infinite generation.
            self._start_infinite_generation()
        else: # Handle unexpected status
            QMessageBox.warning(self, "不明な状態", f"予期せぬ生成ステータスです: {self.generation_status}")
            self.infinite_gen_action.setChecked(False) # Ensure button is unchecked

    def _start_infinite_generation(self):
        """Starts the infinite generation loop."""
        # 生成開始前にゴーストテキストをクリア
        if hasattr(self, 'autocomplete_manager') and self.autocomplete_manager:
            self.autocomplete_manager.clear_ghost_text()
        
        self.generation_status = "infinite_running"
        self.infinite_warning_shown = False # Reset warning flag for new session
        self._update_ui_for_generation_start()

        # Initial prompt build (might be overwritten in loop if immediate update is on)
        # Get raw main text and evaluate dynamic prompts for the initial prompt
        raw_main_text = self.main_text_edit.toPlainText()
        main_text = evaluate_dynamic_prompt(raw_main_text)

        ui_data = self._get_metadata_from_ui() # Get data dict from UI (includes metadata, rating, authors_note)
        # authors_note is evaluated inside build_prompt

        # Load settings for initial prompt build
        settings = load_settings()
        cont_order = settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])

        # Call build_prompt with the new signature for the initial prompt
        self.infinite_generation_prompt = build_prompt(
            current_mode=self.current_mode,
            main_text=main_text,
            ui_data=ui_data, # Pass the whole ui_data dictionary
            cont_prompt_order=cont_order
            # rating_override is no longer needed here, handled inside build_prompt
        )

        self.generation_task = asyncio.ensure_future(self._run_infinite_generation_loop())

    def _stop_current_generation(self):
        """Stops any currently running generation task."""
        if self.generation_status == "idle" or self.generation_task is None:
            return

        current_status_before_stop = self.generation_status
        self.generation_status = "idle" # Set status to idle first

        if current_status_before_stop == "infinite_running":
            self.status_bar.showMessage("無限生成 停止中...", 2000)
        else: # single_running
            self.status_bar.showMessage("単発生成 停止中...", 2000)

        if self.generation_task and not self.generation_task.done():
            self.generation_task.cancel()
            # Set task to None immediately after cancellation request
            self.generation_task = None

        self._update_ui_for_generation_stop()
        # Add a slight delay before final status message if needed
        # QTimer.singleShot(100, lambda: self.status_bar.showMessage("停止中", 3000))
        self.status_bar.showMessage("停止中", 3000)


    def _update_ui_for_generation_start(self):
        """Updates UI elements when generation starts."""
        if self.generation_status == "infinite_running":
            self.infinite_gen_action.setChecked(True)
            self.status_bar.showMessage("無限生成中 (F5で停止)...")
        elif self.generation_status == "single_running":
            self.infinite_gen_action.setChecked(False) # Ensure infinite is unchecked
            self.status_bar.showMessage("単発生成中...")

        # Keep actions enabled so they can be used to stop generation
        # self.single_gen_action.setEnabled(False) # Keep enabled
        # self.infinite_gen_action.setEnabled(False) # Keep enabled
        # The logic within the action handlers (_trigger_single_generation, _toggle_infinite_generation)
        # will determine whether to start or stop based on self.generation_status.

    def _update_ui_for_generation_stop(self):
        """Updates UI elements when generation stops or completes."""
        self.infinite_gen_action.setChecked(False) # Ensure infinite toggle is unchecked
        # Keep actions enabled
        # self.single_gen_action.setEnabled(True)
        # self.infinite_gen_action.setEnabled(True)
        # Status message is set by the calling function (_stop_current_generation or async methods)


    # --- Async Generation Methods ---
    async def _run_single_generation(self, prompt: str, stop_sequence: Optional[List[str]] = None):
        """
        Runs a single generation (for Generate mode or IDEA Fast mode) and updates status.
        Streams output directly to the UI.
        """
        task_name = "アイデア生成 (高速)" if self.current_mode == "idea" else "単発生成"
        try:
            # Get mode-specific max_length
            settings = load_settings()
            if self.current_mode == "idea":
                current_max_length = settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])
            else: # generate mode
                current_max_length = settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])

            # Pass max_length and stop_sequence to generate_stream
            async for token in self.kobold_client.generate_stream(
                prompt,
                max_length=current_max_length,
                stop_sequence=stop_sequence, # Pass the determined stop sequence
                current_mode=self.current_mode # Pass current mode to determine banned tokens
            ):
                self._append_to_output(token)
                await asyncio.sleep(0.001) # Yield control briefly

            # Finished successfully
            self.output_block_counter += 1
            self.status_bar.showMessage(f"{task_name} 完了", 3000)

        except KoboldClientError as e:
            error_msg = f"\n--- {task_name} エラー: {e} ---\n"
            self._append_to_output(error_msg)
            self.status_bar.showMessage(f"{task_name} エラー", 3000)
        except asyncio.CancelledError:
            print(f"{task_name} task cancelled.")
            self._append_to_output(f"\n--- {task_name}がキャンセルされました ---\n")
            self.status_bar.showMessage(f"{task_name} キャンセル", 3000)
        except Exception as e:
            error_msg = f"\n--- {task_name}中に予期せぬエラーが発生しました: {e} ---\n"
            print(error_msg)
            self._append_to_output(error_msg)
            self.status_bar.showMessage("予期せぬエラー", 3000)
        finally:
            # Reset status after single run finishes or errors out
            self.generation_status = "idle"
            self._update_ui_for_generation_stop()
            self.generation_task = None

    async def _run_safe_idea_generation(self, prompt: str, stop_sequence: Optional[List[str]], selected_item_key: str):
        """
        Runs generation for IDEA Safe mode: gets full output, filters, then displays.
        """
        task_name = "アイデア生成 (安全)"
        full_output = ""
        try:
            # Get mode-specific max_length
            settings = load_settings()
            current_max_length = settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])

            # Collect full output from the stream
            async for token in self.kobold_client.generate_stream(
                prompt,
                max_length=current_max_length,
                stop_sequence=stop_sequence,
                current_mode=self.current_mode
            ):
                full_output += token
                # Optional: Add a small sleep if needed, but not strictly necessary here
                # await asyncio.sleep(0.001)

            # Filter the output
            ui_inputs = self._get_metadata_from_ui()["metadata"] # Get current inputs for processor context
            processor = IdeaProcessor(ui_inputs) # Re-instantiate or pass if needed
            filtered_output = processor.filter_output(full_output, selected_item_key)

            # Display filtered output (replace existing content in output area)
            # self._append_to_output(filtered_output) # Append might be confusing, let's replace
            self.output_text_edit.appendPlainText(filtered_output) # Append after the separator
            cursor = self.output_text_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.output_text_edit.setTextCursor(cursor)


            # Finished successfully
            # Increment counter only if generation was successful (or started for streaming)
            # For safe mode, counter is incremented after successful filtering/display
            # For fast mode (including 'all'), counter is incremented after stream finishes in _run_single_generation
            # self.output_block_counter += 1 # Moved to _run_single_generation and after filtering in safe mode
            self.status_bar.showMessage(f"{task_name} 完了", 3000)

        except KoboldClientError as e:
            error_msg = f"\n--- {task_name} エラー: {e} ---\n"
            self._append_to_output(error_msg) # Append errors
            self.status_bar.showMessage(f"{task_name} エラー", 3000)
        except asyncio.CancelledError:
            print(f"{task_name} task cancelled.")
            self._append_to_output(f"\n--- {task_name}がキャンセルされました ---\n") # Append cancellation message
            self.status_bar.showMessage(f"{task_name} キャンセル", 3000)
        except Exception as e:
            error_msg = f"\n--- {task_name}中に予期せぬエラーが発生しました: {e} ---\n"
            print(error_msg)
            self._append_to_output(error_msg) # Append errors
            self.status_bar.showMessage("予期せぬエラー", 3000)
        finally:
            # Reset status after run finishes or errors out
            self.generation_status = "idle"
            self._update_ui_for_generation_stop()
            self.generation_task = None


    async def _run_infinite_generation_loop(self):
        """Continuously generates text, potentially rebuilding the prompt based on settings."""
        settings = load_settings()
        inf_gen_behavior = settings.get("infinite_generation_behavior", DEFAULT_SETTINGS["infinite_generation_behavior"])
        behavior_key = self.current_mode
        update_behavior = inf_gen_behavior.get(behavior_key, "manual")

        # --- Variables to be determined before the loop (for manual) or inside (for immediate) ---
        final_prompt = ""
        stop_sequence = None
        fast_mode_enabled = False
        selected_item_key = "all" # Default for safety
        processor = None
        current_max_length = settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"]) # Default to generate

        # --- Helper function to prepare IDEA generation parameters ---
        def prepare_idea_params():
            nonlocal final_prompt, stop_sequence, fast_mode_enabled, selected_item_key, processor, current_max_length
            try:
                selected_item_index = self.idea_item_combo.currentIndex()
                selected_item_key = self.idea_item_combo.itemData(selected_item_index)
                fast_mode_enabled = self.idea_fast_mode_check.isChecked()
                ui_inputs = self._get_metadata_from_ui()["metadata"]
                full_ui_data = self._get_metadata_from_ui() # For build_prompt

                processor = IdeaProcessor(ui_inputs)
                # Call correct IdeaProcessor methods
                stop_sequence = processor.determine_stop_sequence(selected_item_key)
                prompt_suffix = ""
                warning_msg = None
                if fast_mode_enabled:
                    prereqs_met, warning_msg = processor.check_fast_mode_prerequisites(selected_item_key)
                    # Generate suffix even if prereqs not met, as per single generation logic
                    prompt_suffix = processor.generate_prompt_suffix(selected_item_key)

                if warning_msg:
                    # Show warning only once per infinite generation session
                    if not self.infinite_warning_shown:
                        # Run warning in main thread using QTimer.singleShot
                        QTimer.singleShot(0, lambda: QMessageBox.warning(self, "前提条件に関する警告", warning_msg))
                        self.infinite_warning_shown = True # Set flag after showing

                # Build base prompt
                base_prompt = build_prompt(
                    current_mode="idea",
                    main_text="",
                    ui_data=full_ui_data,
                    cont_prompt_order="reference_first" # Doesn't affect IDEA
                )
                final_prompt = base_prompt + prompt_suffix

                # Get IDEA max length
                current_max_length = settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])

                return True # Preparation successful

            except Exception as e:
                print(f"Error preparing IDEA params: {e}")
                error_msg = f"\n--- 無限生成 (IDEA準備) エラー: {e} ---\n"
                self._append_to_output(error_msg) # Append error to output
                return False # Preparation failed

        # --- Helper function to prepare Generate mode parameters ---
        async def prepare_generate_params():
            """
            無限生成用のGenerateモードプロンプトを動的圧縮込みで構築する。
            is_overflow時はエラー表示用に区別される。
            """
            nonlocal final_prompt, stop_sequence, current_max_length
            try:
                raw_main_text = self.main_text_edit.toPlainText()
                main_text = evaluate_dynamic_prompt(raw_main_text)
                ui_data = self._get_metadata_from_ui()

                current_settings = load_settings()
                cont_order = current_settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])
                compression_mode = current_settings.get("compression_mode", DEFAULT_SETTINGS.get("compression_mode", "token_dynamic"))
                max_len_generate = current_settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])

                base_url = self.kobold_client._get_api_base_url()

                # 圧縮開始前にステータス表示
                QTimer.singleShot(0, lambda: self.status_bar.showMessage("本文圧縮中..."))
                
                # 動的圧縮付きプロンプト構築
                prompt, total_tokens, is_overflow, original_chars, compressed_chars = await build_prompt_with_compression(
                    base_url=base_url,
                    current_mode="generate",
                    main_text=main_text,
                    ui_data=ui_data,
                    cont_prompt_order=cont_order,
                    compression_mode=compression_mode,
                    max_length_generate=max_len_generate,
                )
                
                # 圧縮後、元の無限生成中ステータスに戻す
                QTimer.singleShot(0, lambda: self.status_bar.showMessage("無限生成中 (F5で停止)..."))

                if is_overflow:
                    # 圧縮しても超過 → 無限生成を停止
                    QTimer.singleShot(0, lambda: QMessageBox.critical(
                        self,
                        "コンテキスト長超過により無限生成を停止しました",
                        (
                            "本文と詳細情報を圧縮しましたが、それでもモデルの最大コンテキスト長を超過しているため"
                            "無限生成を停止しました。\n\n"
                            "以下のいずれか、または複数の対応を行ってください。\n"
                            "・詳細情報を推敲して、必要な内容だけを記載する\n"
                            "・KoboldCpp の設定から AI のコンテキスト長を増やす\n"
                            "・最大出力長を減らす\n"
                            "・（非推奨）設定の「最大コンテキスト超過時の処理」で『何もしない』を選択し、このチェックを無視する"
                        ),
                    ))
                    # 無限生成を停止
                    self._append_to_output("\n--- コンテキスト長超過により無限生成を停止しました ---\n")
                    self.status_bar.showMessage("コンテキスト長超過により無限生成を停止しました", 5000)
                    # ループを抜けるためのフラグを設定
                    if self.generation_status == "infinite_running":
                        self.generation_status = "idle"
                        self._update_ui_for_generation_stop()
                    return False

                # 短すぎ警告（無限生成: 最初の1回のみ）
                if (
                    original_chars
                    and compressed_chars is not None
                    and original_chars > 0
                    and not self.infinite_warning_shown
                ):
                    ratio = compressed_chars / float(original_chars)
                    min_ratio = current_settings.get(
                        "warn_short_context_min_ratio",
                        DEFAULT_SETTINGS.get("warn_short_context_min_ratio", 0.5),
                    )
                    min_chars = current_settings.get(
                        "warn_short_context_min_chars",
                        DEFAULT_SETTINGS.get("warn_short_context_min_chars", 2500),
                    )
                    if ratio < min_ratio and compressed_chars < min_chars:
                        def _show_warn():
                            QMessageBox.warning(
                                self,
                                "コンテキスト圧縮に関する警告",
                                (
                                    "詳細情報がAIのコンテキスト（メモリ）に対して大きすぎます。そのため本文が大きく圧縮されており、"
                                    "生成品質が低下している可能性があります。\n\n"
                                    "以下の対応を推奨します。\n"
                                    "・詳細情報を推敲して、必要な内容だけを記載する\n"
                                    "・KoboldCppの設定からAIのコンテキスト長を増やす\n"
                                    "・最大出力長を減らす"
                                ),
                            )
                        QTimer.singleShot(0, _show_warn)
                        self.infinite_warning_shown = True

                final_prompt = prompt
                stop_sequence = None
                current_max_length = max_len_generate
                return True

            except Exception as e:
                print(f"Error preparing Generate params: {e}")
                error_msg = f"\n--- 無限生成 (Generate準備) エラー: {e} ---\n"
                self._append_to_output(error_msg)
                return False

        # --- Initial preparation if behavior is 'manual' ---
        if update_behavior == "manual":
            if self.current_mode == "idea":
                if not prepare_idea_params():
                    self._stop_current_generation()
                    return
            else: # generate mode
                if not await prepare_generate_params():
                    self._stop_current_generation()
                    return
            # Check if initial prompt is empty after manual prep
            if not final_prompt:
                print("Error: Initial infinite generation prompt is empty after manual preparation.")
                self._stop_current_generation() # This line was missing in the previous SEARCH block
                return # Add the missing return statement here
        # --- Main Generation Loop ---
        try:
            while self.generation_status == "infinite_running":
                # --- Re-prepare parameters if behavior is 'immediate' ---
                if update_behavior == "immediate":
                    if self.current_mode == "idea":
                        if not prepare_idea_params():
                            await asyncio.sleep(0.5) # Wait before retrying or stopping
                            continue # Skip this cycle on prep error
                    else: # generate mode
                        if not await prepare_generate_params():
                            await asyncio.sleep(0.5)
                            continue # Skip this cycle on prep error
                    # Check if prompt is empty after immediate prep
                    if not final_prompt:
                        print("Warning: Rebuilt prompt for immediate update is empty. Skipping generation cycle.")
                        await asyncio.sleep(0.5)
                        continue

                # --- Define Separator Dynamically (Inside the loop for immediate mode) ---
                # This needs to happen *after* potential parameter updates in immediate mode
                current_item_text_for_separator = "N/A" # Default
                if self.current_mode == "idea" and self.idea_item_combo:
                    current_item_text_for_separator = self.idea_item_combo.currentText()

                if self.current_mode == "idea":
                    separator = f"\n--- アイデア生成 ({current_item_text_for_separator}) ({self.output_block_counter}) ---\n"
                else: # generate mode
                    separator = f"\n--- 生成ブロック {self.output_block_counter} ---\n"


                # --- Execute Generation based on mode and settings ---
                generation_successful = False
                try:
                    if self.current_mode == "idea":
                        # --- IDEA Mode Execution ---
                        if selected_item_key == "all":
                            # --- "All" Item: Always Stream ---
                            self._append_to_output(separator)
                            async for token in self.kobold_client.generate_stream(
                                final_prompt,
                                max_length=current_max_length,
                                stop_sequence=stop_sequence, # Use determined stop sequence even for 'all'
                                current_mode=self.current_mode # Pass current mode
                            ):
                                if self.generation_status != "infinite_running":
                                    raise asyncio.CancelledError("Infinite generation stopped during stream.")
                                self._append_to_output(token)
                                await asyncio.sleep(0.001)
                            generation_successful = True
                        elif not fast_mode_enabled:
                            # --- Safe Mode (Collect, Filter, Append) ---
                            full_output = ""
                            async for token in self.kobold_client.generate_stream(
                                final_prompt,
                                max_length=current_max_length,
                                stop_sequence=stop_sequence,
                                current_mode=self.current_mode # Already present, no change needed
                            ):
                                if self.generation_status != "infinite_running":
                                    raise asyncio.CancelledError("Infinite generation stopped during stream.")
                                full_output += token
                                # No UI update during collection, maybe a small sleep
                                await asyncio.sleep(0.001)

                            if processor: # Ensure processor exists
                                filtered_output = processor.filter_output(full_output, selected_item_key)
                                # Append separator and filtered output directly
                                self.output_text_edit.appendPlainText(separator + filtered_output)
                                cursor = self.output_text_edit.textCursor()
                                cursor.movePosition(QTextCursor.End)
                                self.output_text_edit.setTextCursor(cursor)
                                generation_successful = True
                            else:
                                print("Error: IdeaProcessor not available for filtering.")
                                self._append_to_output("\n--- フィルタリングエラー ---\n")

                        else:
                            # --- Fast Mode (Stream directly) ---
                            self._append_to_output(separator)
                            async for token in self.kobold_client.generate_stream(
                                final_prompt,
                                max_length=current_max_length,
                                stop_sequence=stop_sequence,
                                current_mode=self.current_mode # Already present, no change needed
                            ):
                                if self.generation_status != "infinite_running":
                                    raise asyncio.CancelledError("Infinite generation stopped during stream.")
                                self._append_to_output(token)
                                await asyncio.sleep(0.001)
                            generation_successful = True

                    else:
                        # --- Generate Mode Execution (Stream directly) ---
                        self._append_to_output(separator)
                        async for token in self.kobold_client.generate_stream(
                            final_prompt,
                            max_length=current_max_length,
                            stop_sequence=stop_sequence, # Will be None for generate mode
                            current_mode=self.current_mode # Pass current mode
                        ):
                            if self.generation_status != "infinite_running":
                                raise asyncio.CancelledError("Infinite generation stopped during stream.")
                            self._append_to_output(token)
                            await asyncio.sleep(0.001)
                        generation_successful = True

                    # --- Post-generation ---
                    if generation_successful:
                        self.output_block_counter += 1
                    await asyncio.sleep(0.5) # Wait before next generation

                except KoboldClientError as e:
                    error_msg = f"\n--- 無限生成中エラー: {e} ---\n"
                    self._append_to_output(error_msg)
                    self.status_bar.showMessage("無限生成エラー発生、停止します", 5000)
                    self._stop_current_generation() # Stop the infinite loop
                    break # Exit while loop
                except asyncio.CancelledError:
                    print("Infinite generation loop cancelled.")
                    # Stop is handled outside, just break the loop
                    break
                except Exception as e:
                    error_msg = f"\n--- 無限生成中に予期せぬエラー: {e} ---\n"
                    print(error_msg)
                    self._append_to_output(error_msg)
                    self.status_bar.showMessage("予期せぬエラー発生、停止します", 5000)
                    self._stop_current_generation() # Stop the infinite loop
                    break # Exit while loop
        finally:
            # Ensure status is reset if loop exits unexpectedly (e.g., error not caught above)
            # or if it finishes normally but wasn't stopped via button click.
            # The _stop_current_generation call inside the loop handles cancellation/errors.
            # This ensures cleanup if the loop condition itself becomes false unexpectedly.
            if self.generation_status == "infinite_running":
                self._stop_current_generation()


    def _append_to_output(self, text: str):
        """Safely appends text to the output area and handles scrolling."""
        cursor = self.output_text_edit.textCursor()
        v_bar = self.output_text_edit.verticalScrollBar()
        is_at_bottom = v_bar.value() >= v_bar.maximum() - 5

        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)

        if is_at_bottom:
            v_bar.setValue(v_bar.maximum())

    def _get_metadata_from_ui(self) -> dict:
        """Retrieves metadata, rating, and author's note from the UI widgets."""
        metadata = { # Initialize the dictionary first
            "title": self.title_edit.text(),
            "keywords": self.keywords_widget.get_tags(),
            "genres": self.genre_widget.get_tags(),
            "synopsis": self.synopsis_edit.toPlainText(),
            "setting": self.setting_edit.toPlainText(),
            "plot": self.plot_edit.toPlainText(),
        }
        # Add dialogue level if selected
        selected_level = self.dialogue_level_combo.currentText()
        if selected_level != "指定なし":
            metadata["dialogue_level"] = selected_level # Add to the dictionary

        # Get the selected rating from the details tab combo box
        selected_rating = self.rating_combo_details.currentData()
        # Get the author's note
        authors_note = self.authors_note_edit.toPlainText()

        return {
            "metadata": metadata,
            "rating": selected_rating,
            "authors_note": authors_note
        }

    async def _cleanup(self): # Make cleanup async
        """Closes the Kobold client when the application is about to quit."""
        print("Cleaning up...")
        if self.generation_status != "idle":
            self._stop_current_generation() # Attempt to stop gracefully
        
        # AutocompleteManagerのクリーンアップ
        if hasattr(self, 'autocomplete_manager'):
            self.autocomplete_manager.cleanup()
        
        print("Requesting Kobold client close...")
        try:
            await self.kobold_client.close() # Await the async close
            print("Kobold client closed.")
        except Exception as e:
            print(f"Error during client close: {e}")

    @Slot()
    def _clear_output_edit(self):
        """Clears the output text edit and resets the block counter."""
        self.output_text_edit.clear()
        self.output_block_counter = 1
        self.status_bar.showMessage("出力エリアをクリアしました。", 2000)

    @Slot()
    def _transfer_output_to_main(self):
        """Transfers selected text from output area to main text area based on settings."""
        selected_text = self.output_text_edit.textCursor().selectedText()
        if not selected_text:
            self.status_bar.showMessage("出力エリアでテキストが選択されていません。", 2000)
            return

        settings = load_settings()
        transfer_mode = settings.get("transfer_to_main_mode", DEFAULT_SETTINGS["transfer_to_main_mode"])
        newlines_before = settings.get("transfer_newlines_before", DEFAULT_SETTINGS["transfer_newlines_before"])

        cursor = self.main_text_edit.textCursor()

        if transfer_mode == "cursor":
            cursor.insertText(selected_text)
        elif transfer_mode == "next_line_always":
            cursor.movePosition(QTextCursor.EndOfLine)
            newlines_to_insert = "\n" * (newlines_before + 1)
            cursor.insertText(newlines_to_insert + selected_text)
        elif transfer_mode == "next_line_eol":
            if cursor.atBlockEnd():
                # Behave like next_line_always if at end of line (block)
                cursor.movePosition(QTextCursor.EndOfLine) # Ensure truly at end
                newlines_to_insert = "\n" * (newlines_before + 1)
                cursor.insertText(newlines_to_insert + selected_text)
            else:
                # Behave like cursor mode if not at end of line
                cursor.insertText(selected_text)
        else: # Fallback to cursor mode if setting is invalid
            cursor.insertText(selected_text)

        self.status_bar.showMessage("選択範囲を本文エリアに転記しました。", 2000)

    @Slot()
    def _transfer_output_to_memo(self): # Renamed from _transfer_main_to_memo
        """Transfers selected text from output area to memo area."""
        selected_text = self.output_text_edit.textCursor().selectedText() # Source is output_text_edit
        if selected_text:
            self.memo_edit.appendPlainText(selected_text) # Append to memo
            self.status_bar.showMessage("選択範囲をメモエリアに転記しました。", 2000)
        else:
            self.status_bar.showMessage("出力エリアでテキストが選択されていません。", 2000) # Message updated

    @Slot()
    def _transfer_idea_to_details(self, metadata_key: str):
        """
        Parses selected text in the output area and transfers the value
        corresponding to the metadata_key to the appropriate details widget.
        """
        selected_text = self.output_text_edit.textCursor().selectedText()
        if not selected_text:
            self.status_bar.showMessage("出力エリアで転記したいテキストを選択してください。", 3000)
            return

        japanese_name_map = {
            "title": "タイトル", "keywords": "キーワード", "genres": "ジャンル",
            "synopsis": "あらすじ", "setting": "設定", "plot": "プロット",
        }
        target_name = japanese_name_map.get(metadata_key)
        if not target_name:
            print(f"Error: Unknown metadata key '{metadata_key}' for transfer.")
            return

        # Find the target section header anywhere in the selection and capture everything after it
        pattern = re.compile(rf"# {re.escape(target_name)}:\s*(.*)", re.MULTILINE | re.DOTALL)
        match = pattern.search(selected_text)

        if not match:
            self.status_bar.showMessage(f"選択範囲から「{target_name}」セクションが見つかりませんでした。", 3000)
            return

        # Extract content after the header and process line by line
        content_after_header = match.group(1).strip()
        lines = content_after_header.splitlines()
        extracted_lines = []
        for line in lines:
            # Check if the line starts with another section header
            is_next_header = False
            # Iterate through all possible Japanese names in the map
            for key, jp_name in japanese_name_map.items():
                # Make sure we don't stop at the *current* header if it appears again,
                # only stop if it's a *different* header.
                if key != metadata_key and line.strip().startswith(f"# {jp_name}:"):
                    is_next_header = True
                    break # Found a different header, stop checking for this line
            
            if is_next_header:
                break # Stop extracting lines when the next header is found
            extracted_lines.append(line) # Append the line if it's not a subsequent header

        extracted_value = "\n".join(extracted_lines).strip() # Join the extracted lines

        # Handle potential empty extraction if the target header was last or immediately followed
        # (extracted_value might be "" here, which is generally okay, but check specific cases)

        try:
            if metadata_key == "title":
                # Title should be single line, take the first extracted line
                extracted_value = extracted_value.splitlines()[0] if extracted_value else ""
                self.title_edit.setText(extracted_value)
            elif metadata_key == "keywords":
                tags = [line.strip().lstrip('-').strip() for line in extracted_value.splitlines() if line.strip()]
                self.keywords_widget.set_tags(tags)
            elif metadata_key == "genres":
                tags = [line.strip().lstrip('-').strip() for line in extracted_value.splitlines() if line.strip()]
                self.genre_widget.set_tags(tags)
            elif metadata_key == "synopsis":
                self.synopsis_edit.setPlainText(extracted_value)
            elif metadata_key == "setting":
                self.setting_edit.setPlainText(extracted_value)
            elif metadata_key == "plot":
                self.plot_edit.setPlainText(extracted_value)
            else:
                print(f"Error: No widget defined for key '{metadata_key}'.")
                return

            self.status_bar.showMessage(f"「{target_name}」を詳細情報に転記しました。", 2000)

        except Exception as e:
            print(f"Error transferring data for '{metadata_key}': {e}")
            self.status_bar.showMessage(f"「{target_name}」の転記中にエラーが発生しました。", 3000)


    @Slot()
    def _set_mode_generate(self):
        """Sets the application mode to 'generate'."""
        if self.generation_status != "idle":
            QMessageBox.warning(self, "生成中", "生成中にモードは変更できません。")
            self.idea_mode_action.setChecked(self.current_mode == "idea") # Revert check state
            self.gen_mode_action.setChecked(self.current_mode == "generate")
            return
        self.current_mode = "generate"
        self.status_bar.showMessage("モード: 小説生成", 2000)
        if self.idea_controls_widget:
            self.idea_controls_widget.hide()
        # 小説生成モードに戻った時、本文補完機能のチェックボックスを有効化（チェック状態はOFFに戻す）
        self.autocomplete_checkbox.setEnabled(True)
        self.autocomplete_checkbox.setChecked(False)
        if hasattr(self, 'autocomplete_manager'):
            self.autocomplete_manager.set_enabled(False)
        
        # ショートカット表示を更新
        self._update_shortcut_display()

    @Slot()
    def _set_mode_idea(self):
        """Sets the application mode to 'idea'."""
        if self.generation_status != "idle":
            QMessageBox.warning(self, "生成中", "生成中にモードは変更できません。")
            self.idea_mode_action.setChecked(self.current_mode == "idea") # Revert check state
            self.gen_mode_action.setChecked(self.current_mode == "generate")
            return
        self.current_mode = "idea"
        self.status_bar.showMessage("モード: アイデア出し", 2000)
        if self.idea_controls_widget:
            self.idea_controls_widget.show()
            self._update_idea_fast_mode_state() # Update checkbox state when switching to idea mode
            # アイデア出しモードでは本文補完機能を無効化
            self.autocomplete_checkbox.setEnabled(False)
            self.autocomplete_checkbox.setChecked(False)
            if hasattr(self, 'autocomplete_manager'):
                self.autocomplete_manager.set_enabled(False)
            
            # ショートカット表示を更新
            self._update_shortcut_display()

    @Slot()
    def _toggle_autocomplete_mode(self, checked):
        """Toggle autocomplete mode with exclusive control."""
        if checked:
            # 執筆支援モードをONにする時、無限生成が動いていれば停止
            if self.generation_status == "infinite_running":
                QMessageBox.information(self, "無限生成停止", "無限生成を停止して、本文補完機能を有効化しました。")
                self._stop_current_generation()
            # オートコンプリートを有効化
            if hasattr(self, 'autocomplete_manager'):
                self.autocomplete_manager.set_enabled(True)
                # 有効化直後にデバウンスタイマーを開始して、すぐにオートコンプリートが動作するようにする
                self.autocomplete_manager.debounce_timer.start(self.autocomplete_manager.debounce_ms)
            self.status_bar.showMessage("本文補完機能: ON", 2000)
        else:
            # 執筆支援モードをOFFにする時
            if hasattr(self, 'autocomplete_manager'):
                self.autocomplete_manager.set_enabled(False)
            self.status_bar.showMessage("本文補完機能: OFF", 2000)
        
        # ショートカット表示を更新
        self._update_shortcut_display()

    @Slot()
    def _update_idea_fast_mode_state(self):
        """Enables/disables the fast mode checkbox based on combo box selection."""
        if not self.idea_item_combo or not self.idea_fast_mode_check:
            return

        selected_item_index = self.idea_item_combo.currentIndex()
        selected_item_key = self.idea_item_combo.itemData(selected_item_index)

        # Disable fast mode for "全部" or the first item ("タイトル")
        if selected_item_key == 'all' or selected_item_key == IDEA_ITEM_ORDER[0]:
            self.idea_fast_mode_check.setEnabled(False)
            self.idea_fast_mode_check.setChecked(False) # Uncheck when disabled
        else:
            self.idea_fast_mode_check.setEnabled(True)

    def eventFilter(self, obj, event):
        """
        イベントフィルター - main_text_editのキーイベントを処理
        
        Args:
            obj: イベントを受けたオブジェクト
            event: イベント
            
        Returns:
            イベントを処理した場合True
        """
        if obj == self.main_text_edit and self.autocomplete_manager:
            # IME入力イベント（日本語入力開始時にゴーストテキストを消去）
            if event.type() == QEvent.Type.InputMethod:
                if self.autocomplete_checkbox.isChecked() and self.autocomplete_manager.has_ghost_text():
                    self.autocomplete_manager.clear_ghost_text()
                return False  # イベントを握りつぶさない
            
            # キー押下イベント
            if event.type() == QEvent.Type.KeyPress:
                # Ctrl+Space: 手動でオートコンプリートをトリガー（補完機能が有効な場合のみ）
                if (event.key() == Qt.Key_Space and
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier and
                    self.autocomplete_checkbox.isChecked()):
                    self.autocomplete_manager.trigger_now()
                    return True
                
                # その他のキーイベントはAutocompleteManagerに委譲（補完機能が有効な場合のみ）
                if self.autocomplete_checkbox.isChecked() and self.autocomplete_manager.handle_key_press(event):
                    return True
        
        return super().eventFilter(obj, event)

    @Slot()
    def _on_token_timer_timeout(self):
        """Handles token update timer timeout."""
        # Use asyncio.ensure_future to run async method without blocking
        asyncio.ensure_future(self._update_token_display())

    async def _update_token_display(self):
        """Updates the token display in the status bar."""
        # Get current main text
        main_text = self.main_text_edit.toPlainText()
        main_text_chars = len(main_text)
        
        # Skip if no text
        if main_text_chars == 0:
            self.token_label.setText("本文文字数0文字(0トークン) | 全プロンプト: 0 / 0トークン")
            return
            
        # Calculate main text tokens (approximate for now, can be improved with actual API call)
        # Rough approximation: 1 token ≈ 0.75 characters for Japanese text
        main_text_tokens = int(main_text_chars * 0.75)
        
        # Build current prompt to get total tokens
        try:
            ui_data = self._get_metadata_from_ui()
            settings = load_settings()
            cont_order = settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])
            
            # Build prompt without compression for token counting
            prompt = build_prompt(
                current_mode=self.current_mode,
                main_text=main_text,
                ui_data=ui_data,
                cont_prompt_order=cont_order
            )
            
            # Get max output length based on mode
            if self.current_mode == "idea":
                max_output = settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"])
            else:
                max_output = settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"])
            
            # Get available context (max_context - max_output)
            base_url = self.kobold_client._get_api_base_url()
            
            # Use direct async calls instead of run_until_complete
            max_context = await get_true_max_context_length(base_url)
            if max_context is None:
                max_context = 20000  # Fallback value
                
            available_context = max_context - max_output
            
            # Count tokens in prompt
            prompt_tokens = await count_tokens(base_url, prompt)
            if prompt_tokens is None:
                prompt_tokens = len(prompt) // 4  # Fallback approximation
                
            # Check if compression is needed
            compression_needed = prompt_tokens > available_context
            
            # Format display
            if compression_needed:
                status_text = f"本文文字数{main_text_chars:,}文字({main_text_tokens:,}トークン) | 全プロンプト: {prompt_tokens:,} / {available_context:,}トークン (要圧縮)"
            else:
                status_text = f"本文文字数{main_text_chars:,}文字({main_text_tokens:,}トークン) | 全プロンプト: {prompt_tokens:,} / {available_context:,}トークン"
                
            self.token_label.setText(status_text)
            
        except Exception as e:
            # Fallback display on error
            self.token_label.setText(f"本文文字数{main_text_chars:,}文字(計算中...) | 全プロンプト: 計算中 / 0トークン")
            print(f"Token calculation error: {e}")
    
    def _update_shortcut_display(self):
        """ショートカットキーの表示を更新する"""
        if not hasattr(self, 'shortcut_label'):
            return
            
        # 基本のショートカット
        base_shortcuts = "単発生成: Ctrl+G | 無限生成: F5"
        
        # 本文補完機能が有効な場合の追加ショートカット（共通表示）
        if self.autocomplete_checkbox.isChecked():
            autocomplete_shortcuts = " || 確定: Tab | キャンセル: Esc | 手動補完: Ctrl+Space"
            self.shortcut_label.setText(base_shortcuts + autocomplete_shortcuts)
        else:
            self.shortcut_label.setText(base_shortcuts)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    async def async_cleanup():
        await window._cleanup()
    app.aboutToQuit.connect(lambda: asyncio.ensure_future(async_cleanup()))
    window.show()

    with loop:
        loop.run_forever()
