from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
                               QDoubleSpinBox, QTextEdit, QFormLayout, QComboBox,
                               QDialogButtonBox, QWidget, QGroupBox, QRadioButton,
                               QSpacerItem, QSizePolicy, QPlainTextEdit)
from PySide6.QtCore import Slot
from src.core.settings import load_settings, save_settings, DEFAULT_SETTINGS
from src.ui.widgets import CollapsibleSection

class KoboldConfigDialog(QDialog):
    """Dialog for configuring KoboldCpp connection settings."""
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("KoboldCpp 設定")

        self.current_settings = load_settings()

        layout = QVBoxLayout(self)

        # Port setting
        port_layout = QHBoxLayout()
        port_label = QLabel("KoboldCpp API Port:")
        self.port_spinbox = QSpinBox()
        self.port_spinbox.setRange(1, 65535)
        self.port_spinbox.setValue(self.current_settings.get("kobold_port", 5001))
        port_layout.addWidget(port_label)
        port_layout.addWidget(self.port_spinbox)
        layout.addLayout(port_layout)

        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept(self):
        """Saves the settings when OK is clicked."""
        self.current_settings["kobold_port"] = self.port_spinbox.value()
        save_settings(self.current_settings)
        super().accept()

    @staticmethod
    def show_dialog(parent: QWidget | None = None) -> bool:
        """Creates and shows the dialog, returning True if accepted."""
        dialog = KoboldConfigDialog(parent)
        return dialog.exec() == QDialog.Accepted


class ChatTemplateModeStartupDialog(QDialog):
    """Startup dialog for selecting the default chat template mode."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("チャットテンプレモードの選択")
        self.setMinimumWidth(560)

        self.current_settings = load_settings()

        layout = QVBoxLayout(self)

        intro_label = QLabel(
            "使用するモデルに応じてチャットテンプレモードを選択してください。"
        )
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        model_info = QLabel(
            "次のモデルを使っている場合は `wanabiシリーズ` を選択してください。\n"
            "kawaimasa/Wanabi-Novelist-24B-GGUF\n"
            "kawaimasa/Wanabi-Novelist-12B-GGUF\n"
            "kawaimasa/wanabi_24b_v1_GGUF\n"
            "kawaimasa/wanabi_mini_12b_GGUF\n\n"
            "それ以外のモデルは `汎用` を選択してください。"
        )
        model_info.setWordWrap(True)
        layout.addWidget(model_info)

        mode_group = QGroupBox("チャットテンプレモード")
        mode_layout = QVBoxLayout(mode_group)
        self.legacy_radio = QRadioButton("wanabiシリーズ")
        self.generic_radio = QRadioButton("汎用")
        mode_layout.addWidget(self.legacy_radio)
        mode_layout.addWidget(self.generic_radio)
        layout.addWidget(mode_group)

        current_mode = self.current_settings.get(
            "prompt_delivery_mode",
            DEFAULT_SETTINGS.get("prompt_delivery_mode", "chat_completions_generic"),
        )
        if current_mode == "mistral_legacy":
            self.legacy_radio.setChecked(True)
        else:
            self.generic_radio.setChecked(True)

        note_label = QLabel("この設定はあとから設定画面から変更できます。")
        note_label.setWordWrap(True)
        layout.addWidget(note_label)

        from PySide6.QtWidgets import QCheckBox
        self.skip_checkbox = QCheckBox("起動時にポップアップしない")
        self.skip_checkbox.setChecked(
            self.current_settings.get(
                "skip_template_mode_prompt_on_startup",
                DEFAULT_SETTINGS.get("skip_template_mode_prompt_on_startup", False),
            )
        )
        layout.addWidget(self.skip_checkbox)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def accept(self):
        self.current_settings["prompt_delivery_mode"] = (
            "mistral_legacy" if self.legacy_radio.isChecked() else "chat_completions_generic"
        )
        self.current_settings["skip_template_mode_prompt_on_startup"] = self.skip_checkbox.isChecked()
        save_settings(self.current_settings)
        super().accept()

class GenerationParamsDialog(QDialog):
    """Dialog for configuring LLM generation parameters."""
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("生成パラメータ設定")
        self.setMinimumWidth(400) # Set a minimum width

        self.current_settings = load_settings()

        main_layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # 最大出力長 (モード別)
        self.max_length_idea_spinbox = QSpinBox()
        self.max_length_idea_spinbox.setRange(1, 10000) # Adjust max as needed
        self.max_length_idea_spinbox.setValue(self.current_settings.get("max_length_idea", DEFAULT_SETTINGS["max_length_idea"]))
        form_layout.addRow("最大出力長 (アイデア出し):", self.max_length_idea_spinbox)

        self.max_length_generate_spinbox = QSpinBox()
        self.max_length_generate_spinbox.setRange(1, 10000) # Adjust max as needed
        self.max_length_generate_spinbox.setValue(self.current_settings.get("max_length_generate", DEFAULT_SETTINGS["max_length_generate"]))
        form_layout.addRow("最大出力長 (小説生成/継続):", self.max_length_generate_spinbox)

        # temperature
        self.temp_spinbox = QDoubleSpinBox()
        self.temp_spinbox.setRange(0.0, 5.0) # Allow higher temps if needed
        self.temp_spinbox.setSingleStep(0.05)
        self.temp_spinbox.setDecimals(2)
        self.temp_spinbox.setValue(self.current_settings.get("temperature", DEFAULT_SETTINGS["temperature"]))
        form_layout.addRow("Temperature:", self.temp_spinbox)

        # min_p
        self.min_p_spinbox = QDoubleSpinBox()
        self.min_p_spinbox.setRange(0.0, 1.0)
        self.min_p_spinbox.setSingleStep(0.01)
        self.min_p_spinbox.setDecimals(2)
        self.min_p_spinbox.setValue(self.current_settings.get("min_p", DEFAULT_SETTINGS["min_p"]))
        form_layout.addRow("Min P:", self.min_p_spinbox)

        # top_p
        self.top_p_spinbox = QDoubleSpinBox()
        self.top_p_spinbox.setRange(0.0, 1.0)
        self.top_p_spinbox.setSingleStep(0.01)
        self.top_p_spinbox.setDecimals(2)
        self.top_p_spinbox.setValue(self.current_settings.get("top_p", DEFAULT_SETTINGS["top_p"]))
        form_layout.addRow("Top P:", self.top_p_spinbox)

        # top_k
        self.top_k_spinbox = QSpinBox()
        self.top_k_spinbox.setRange(0, 200) # 0 means disabled for KoboldCpp usually
        self.top_k_spinbox.setValue(self.current_settings.get("top_k", DEFAULT_SETTINGS["top_k"]))
        form_layout.addRow("Top K:", self.top_k_spinbox)

        # rep_pen
        self.rep_pen_spinbox = QDoubleSpinBox()
        self.rep_pen_spinbox.setRange(1.0, 5.0) # Adjust max as needed
        self.rep_pen_spinbox.setSingleStep(0.01)
        self.rep_pen_spinbox.setDecimals(2)
        self.rep_pen_spinbox.setValue(self.current_settings.get("rep_pen", DEFAULT_SETTINGS["rep_pen"]))
        form_layout.addRow("Repetition Penalty:", self.rep_pen_spinbox)

        # --- Default Rating Setting ---
        rating_label = QLabel("デフォルトレーティング:")
        self.rating_combo = QComboBox()
        self.rating_combo.addItem("General (全年齢)", "general")
        self.rating_combo.addItem("R-18", "r18")
        form_layout.addRow(rating_label, self.rating_combo)
        # Load initial rating setting
        current_rating = self.current_settings.get("default_rating", DEFAULT_SETTINGS["default_rating"])
        rating_index = self.rating_combo.findData(current_rating)
        if rating_index != -1:
            self.rating_combo.setCurrentIndex(rating_index)
        # --- End Default Rating Setting ---

        self.prompt_delivery_combo = QComboBox()
        self.prompt_delivery_combo.addItem("wanabiシリーズ", "mistral_legacy")
        self.prompt_delivery_combo.addItem("汎用", "chat_completions_generic")
        current_delivery_mode = self.current_settings.get("prompt_delivery_mode", DEFAULT_SETTINGS.get("prompt_delivery_mode", "mistral_legacy"))
        delivery_index = self.prompt_delivery_combo.findData(current_delivery_mode)
        if delivery_index != -1:
            self.prompt_delivery_combo.setCurrentIndex(delivery_index)
        form_layout.addRow("チャットテンプレモード:", self.prompt_delivery_combo)

        self.system_prompt_edit = QTextEdit()
        self.system_prompt_edit.setAcceptRichText(False)
        self.system_prompt_edit.setMinimumHeight(90)
        self.system_prompt_edit.setPlaceholderText("Generic mode でのみ使用される system prompt")
        self.system_prompt_edit.setPlainText(self.current_settings.get("system_prompt", DEFAULT_SETTINGS.get("system_prompt", "")))
        form_layout.addRow("System Prompt:", self.system_prompt_edit)

        self.thinking_template_preset_combo = QComboBox()
        self.thinking_template_preset_combo.addItem("Gemma 4", "gemma4")
        self.thinking_template_preset_combo.addItem("思考を無効化", "disabled")
        preset_index = self.thinking_template_preset_combo.findData(
            self.current_settings.get(
                "thinking_template_preset",
                DEFAULT_SETTINGS.get("thinking_template_preset", "gemma4"),
            )
        )
        if preset_index != -1:
            self.thinking_template_preset_combo.setCurrentIndex(preset_index)
        form_layout.addRow("Thinking Template:", self.thinking_template_preset_combo)

        main_layout.addLayout(form_layout)

        prefill_thinking_section = CollapsibleSection("prefill と思考モード")
        prefill_thinking_group = QGroupBox()
        prefill_thinking_layout = QVBoxLayout(prefill_thinking_group)

        prefill_thinking_desc = QLabel(
            "assistant prefill と思考モードが衝突する場合の処理です。"
            " disabled 以外では通常生成/アイデア生成で二段階生成を行い、"
            " 1回目の思考抽出に失敗した場合は本番生成を中断します。"
        )
        prefill_thinking_desc.setWordWrap(True)
        prefill_thinking_layout.addWidget(prefill_thinking_desc)

        self.prefill_thinking_strategy_combo = QComboBox()
        self.prefill_thinking_strategy_combo.addItem("disabled (思考を自動OFF)", "disabled")
        self.prefill_thinking_strategy_combo.addItem("think_tags (<think>...</think>)", "think_tags")
        self.prefill_thinking_strategy_combo.addItem("gemma4_channel (<|channel>thought ...)", "gemma4_channel")
        self.prefill_thinking_strategy_combo.addItem("custom", "custom")
        strategy_index = self.prefill_thinking_strategy_combo.findData(
            self.current_settings.get(
                "prefill_thinking_strategy",
                DEFAULT_SETTINGS.get("prefill_thinking_strategy", "disabled"),
            )
        )
        if strategy_index != -1:
            self.prefill_thinking_strategy_combo.setCurrentIndex(strategy_index)
        prefill_thinking_layout.addWidget(self.prefill_thinking_strategy_combo)

        self.prefill_thinking_custom_prefix_edit = QPlainTextEdit()
        self.prefill_thinking_custom_prefix_edit.setPlaceholderText("custom prefix")
        self.prefill_thinking_custom_prefix_edit.setMaximumHeight(70)
        self.prefill_thinking_custom_prefix_edit.setPlainText(
            self.current_settings.get(
                "prefill_thinking_custom_prefix",
                DEFAULT_SETTINGS.get("prefill_thinking_custom_prefix", ""),
            )
        )
        prefill_thinking_layout.addWidget(QLabel("Custom Prefix:"))
        prefill_thinking_layout.addWidget(self.prefill_thinking_custom_prefix_edit)

        self.prefill_thinking_custom_suffix_edit = QPlainTextEdit()
        self.prefill_thinking_custom_suffix_edit.setPlaceholderText("custom suffix")
        self.prefill_thinking_custom_suffix_edit.setMaximumHeight(70)
        self.prefill_thinking_custom_suffix_edit.setPlainText(
            self.current_settings.get(
                "prefill_thinking_custom_suffix",
                DEFAULT_SETTINGS.get("prefill_thinking_custom_suffix", ""),
            )
        )
        prefill_thinking_layout.addWidget(QLabel("Custom Suffix:"))
        prefill_thinking_layout.addWidget(self.prefill_thinking_custom_suffix_edit)

        prefill_thinking_section.addWidget(prefill_thinking_group)
        main_layout.addWidget(prefill_thinking_section)

        # 本文圧縮モード設定 - CollapsibleSectionで囲む
        compression_section = CollapsibleSection("最大コンテキスト超過時の処理")
        compression_group = QGroupBox()
        compression_layout = QVBoxLayout(compression_group)

        self.compression_combo = QComboBox()
        self.compression_combo.addItem("本文をトークン数に基づき圧縮 (推奨)", "token_dynamic")
        self.compression_combo.addItem("最大本文文字数にトリム", "char_trim")
        self.compression_combo.addItem("何もしない (非推奨)", "none")
        current_mode = self.current_settings.get("compression_mode", DEFAULT_SETTINGS.get("compression_mode", "token_dynamic"))
        index = self.compression_combo.findData(current_mode)
        if index != -1:
            self.compression_combo.setCurrentIndex(index)
        compression_layout.addWidget(self.compression_combo)

        # 最大本文文字数 (char_trim専用)
        max_main_text_label = QLabel("最大本文文字数 (char_trim選択時に有効):")
        self.max_main_text_chars_spinbox = QSpinBox()
        self.max_main_text_chars_spinbox.setRange(1, 32768)
        self.max_main_text_chars_spinbox.setValue(self.current_settings.get("max_main_text_chars", DEFAULT_SETTINGS.get("max_main_text_chars", 8000)))
        compression_layout.addWidget(max_main_text_label)
        compression_layout.addWidget(self.max_main_text_chars_spinbox)

        # トークン数ベース圧縮ステップ (token_dynamic専用)
        token_step_label = QLabel("トークン数ベース圧縮: 一度に削る文字数 (大きくすると高速化、小さくすると正確に):")
        self.token_compression_step_spinbox = QSpinBox()
        self.token_compression_step_spinbox.setRange(1, 5000)
        self.token_compression_step_spinbox.setValue(self.current_settings.get("token_compression_step_chars", DEFAULT_SETTINGS.get("token_compression_step_chars", 100)))
        compression_layout.addWidget(token_step_label)
        compression_layout.addWidget(self.token_compression_step_spinbox)

        compression_section.addWidget(compression_group)
        main_layout.addWidget(compression_section)

        # --- Generation Control Section (Stop Sequences & Banned Tokens) ---
        gen_control_section = CollapsibleSection("生成制御 (ストップシーケンス・禁止ワード)")
        gen_control_group = QGroupBox()
        gen_control_layout = QVBoxLayout(gen_control_group)

        # Stop Sequences
        stop_seq_label = QLabel("ストップシーケンス (Legacy prompt mode 用 / 1行に1つ):")
        self.stop_seq_edit = QTextEdit()
        self.stop_seq_edit.setAcceptRichText(False)
        self.stop_seq_edit.setPlaceholderText("例:\n[INST]\n[/INST]\n<|endoftext|>")
        stop_sequences = self.current_settings.get("stop_sequences", DEFAULT_SETTINGS["stop_sequences"])
        self.stop_seq_edit.setText("\n".join(stop_sequences))
        gen_control_layout.addWidget(stop_seq_label)
        gen_control_layout.addWidget(self.stop_seq_edit)

        # Banned Tokens (Phrase Banning)
        banned_tokens_label = QLabel("禁止ワード (1行に1つ):")
        self.banned_tokens_edit = QTextEdit()
        self.banned_tokens_edit.setAcceptRichText(False)
        self.banned_tokens_edit.setPlaceholderText("例:\n<|endoftext|>\n特定の単語")
        banned_tokens = self.current_settings.get("banned_tokens", DEFAULT_SETTINGS["banned_tokens"])
        self.banned_tokens_edit.setText("\n".join(banned_tokens))
        gen_control_layout.addWidget(banned_tokens_label)
        gen_control_layout.addWidget(self.banned_tokens_edit)

        gen_control_section.addWidget(gen_control_group)
        main_layout.addWidget(gen_control_section)
        # --- End Generation Control Section ---

        # --- Continuation Prompt Order Setting ---
        cont_order_section = CollapsibleSection("継続タスクのプロンプト順序")
        cont_order_group = QGroupBox()
        cont_order_layout = QVBoxLayout(cont_order_group)

        self.cont_order_combo = QComboBox()
        # Add items with display text and internal data
        self.cont_order_combo.addItem("小説継続タスク: 本文との整合性を優先 (推奨)", "reference_first")
        self.cont_order_combo.addItem("小説継続タスク: 詳細情報との整合性を優先", "text_first")

        # Load initial setting and set combo box index
        current_cont_order = self.current_settings.get("cont_prompt_order", DEFAULT_SETTINGS["cont_prompt_order"])
        index_to_set = self.cont_order_combo.findData(current_cont_order)
        if index_to_set != -1:
            self.cont_order_combo.setCurrentIndex(index_to_set)

        cont_order_layout.addWidget(self.cont_order_combo)

        cont_order_desc_label = QLabel("(低コンテキスト設定では「詳細情報との整合性を優先」が有効な場合があります)")
        cont_order_desc_label.setWordWrap(True) # Allow text wrapping
        cont_order_layout.addWidget(cont_order_desc_label)

        cont_order_section.addWidget(cont_order_group)
        main_layout.addWidget(cont_order_section)
        # --- End Continuation Prompt Order Setting ---


        # --- Infinite Generation Behavior Settings ---
        inf_gen_section = CollapsibleSection("無限生成中のプロンプト更新")
        inf_gen_group = QGroupBox()
        inf_gen_layout = QVBoxLayout(inf_gen_group)

        # Idea Mode Behavior
        idea_group = QGroupBox("アイデア出しモード時")
        idea_layout = QHBoxLayout(idea_group)
        self.idea_immediate_radio = QRadioButton("詳細情報の変更を即時反映")
        self.idea_manual_radio = QRadioButton("生成停止/再開まで変更を反映しない (手動)")
        idea_layout.addWidget(self.idea_immediate_radio)
        idea_layout.addWidget(self.idea_manual_radio)
        inf_gen_layout.addWidget(idea_group)

        # Generate Mode Behavior
        gen_group = QGroupBox("小説生成モード時")
        gen_layout = QHBoxLayout(gen_group)
        self.gen_immediate_radio = QRadioButton("詳細情報/本文の変更を即時反映")
        self.gen_manual_radio = QRadioButton("生成停止/再開まで変更を反映しない (手動)")
        gen_layout.addWidget(self.gen_immediate_radio)
        gen_layout.addWidget(self.gen_manual_radio)
        inf_gen_layout.addWidget(gen_group)

        inf_gen_section.addWidget(inf_gen_group)
        main_layout.addWidget(inf_gen_section)

        # Load initial state for radio buttons
        inf_gen_behavior = self.current_settings.get("infinite_generation_behavior", DEFAULT_SETTINGS["infinite_generation_behavior"])
        if inf_gen_behavior.get("idea", "manual") == "immediate":
            self.idea_immediate_radio.setChecked(True)
        else:
            self.idea_manual_radio.setChecked(True)

        if inf_gen_behavior.get("generate", "manual") == "immediate":
            self.gen_immediate_radio.setChecked(True)
        else:
            self.gen_manual_radio.setChecked(True)

        # --- Transfer to Main Text Settings ---
        transfer_section = CollapsibleSection("出力から本文への転記設定")
        transfer_group = QGroupBox()
        transfer_layout = QVBoxLayout(transfer_group)

        # Transfer Mode Radio Buttons
        transfer_mode_layout = QHBoxLayout()
        self.transfer_cursor_radio = QRadioButton("カーソル位置に挿入")
        self.transfer_next_always_radio = QRadioButton("常に次の行に挿入")
        self.transfer_next_eol_radio = QRadioButton("行末の場合のみ次の行に挿入")
        transfer_mode_layout.addWidget(self.transfer_cursor_radio)
        transfer_mode_layout.addWidget(self.transfer_next_always_radio)
        transfer_mode_layout.addWidget(self.transfer_next_eol_radio)
        transfer_layout.addLayout(transfer_mode_layout)

        # Newlines Before Transfer SpinBox
        newline_layout = QHBoxLayout()
        newline_label = QLabel("次の行に挿入する際の追加空行数:")
        self.transfer_newlines_spinbox = QSpinBox()
        self.transfer_newlines_spinbox.setRange(0, 5) # Allow 0 to 5 empty lines
        self.transfer_newlines_spinbox.setValue(self.current_settings.get("transfer_newlines_before", DEFAULT_SETTINGS["transfer_newlines_before"]))
        newline_layout.addWidget(newline_label)
        newline_layout.addWidget(self.transfer_newlines_spinbox)
        newline_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)) # Add spacer
        transfer_layout.addLayout(newline_layout)

        transfer_section.addWidget(transfer_group)
        main_layout.addWidget(transfer_section)

        # Load initial state for transfer settings
        transfer_mode = self.current_settings.get("transfer_to_main_mode", DEFAULT_SETTINGS["transfer_to_main_mode"])
        if transfer_mode == "next_line_always":
            self.transfer_next_always_radio.setChecked(True)
        elif transfer_mode == "next_line_eol":
            self.transfer_next_eol_radio.setChecked(True)
        else: # Default to cursor
            self.transfer_cursor_radio.setChecked(True)

        # Connect radio buttons to enable/disable spinbox
        self.transfer_cursor_radio.toggled.connect(self._update_newline_spinbox_state)
        self.transfer_next_always_radio.toggled.connect(self._update_newline_spinbox_state)
        self.transfer_next_eol_radio.toggled.connect(self._update_newline_spinbox_state)
        self._update_newline_spinbox_state() # Set initial state
        self.prefill_thinking_strategy_combo.currentIndexChanged.connect(self._update_prefill_thinking_custom_state)
        self.thinking_template_preset_combo.currentIndexChanged.connect(self._sync_prefill_thinking_strategy_to_template)
        self._update_prefill_thinking_custom_state()
        self._sync_prefill_thinking_strategy_to_template()

        # --- Author's Note Display Mode Setting ---
        authors_note_group = QGroupBox("プロンプトフォーマット")
        authors_note_layout = QVBoxLayout(authors_note_group)

        self.authors_note_combo = QComboBox()
        self.authors_note_combo.addItem("レガシー（wanabi_24b_v1やminiなど使用する場合はこちら）", "legacy")
        self.authors_note_combo.addItem("デフォルト（v3以降のモデルのモデルはこちら）", "default")

        # Load initial setting
        current_authors_note_mode = self.current_settings.get("authors_note_display_mode", DEFAULT_SETTINGS["authors_note_display_mode"])
        index_to_set = self.authors_note_combo.findData(current_authors_note_mode)
        if index_to_set != -1:
            self.authors_note_combo.setCurrentIndex(index_to_set)

        authors_note_layout.addWidget(self.authors_note_combo)
        main_layout.addWidget(authors_note_group)

        # 折りたたみセクションを初期状態で閉じる
        compression_section.toggle_button.setChecked(False)
        prefill_thinking_section.toggle_button.setChecked(False)
        gen_control_section.toggle_button.setChecked(False)
        cont_order_section.toggle_button.setChecked(False)
        inf_gen_section.toggle_button.setChecked(False)
        transfer_section.toggle_button.setChecked(False)

        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def accept(self):
        """Saves the settings when OK is clicked."""
        # self.current_settings["max_length"] = self.max_length_spinbox.value() # Removed old setting
        self.current_settings["max_length_idea"] = self.max_length_idea_spinbox.value()
        self.current_settings["max_length_generate"] = self.max_length_generate_spinbox.value()
        self.current_settings["temperature"] = self.temp_spinbox.value()
        self.current_settings["min_p"] = self.min_p_spinbox.value()
        self.current_settings["top_p"] = self.top_p_spinbox.value()
        self.current_settings["top_k"] = self.top_k_spinbox.value() # Save Top-K
        self.current_settings["rep_pen"] = self.rep_pen_spinbox.value()

        # Process stop sequences: split by newline, strip whitespace, remove empty lines
        stop_sequences_text = self.stop_seq_edit.toPlainText()
        stop_sequences_list = [line.strip() for line in stop_sequences_text.splitlines() if line.strip()]
        self.current_settings["stop_sequences"] = stop_sequences_list

        # Process banned tokens: split by newline, strip whitespace, remove empty lines
        banned_tokens_text = self.banned_tokens_edit.toPlainText()
        banned_tokens_list = [line.strip() for line in banned_tokens_text.splitlines() if line.strip()]
        self.current_settings["banned_tokens"] = banned_tokens_list

        # Save continuation prompt order setting
        self.current_settings["cont_prompt_order"] = self.cont_order_combo.currentData()

        # Save compression settings
        self.current_settings["compression_mode"] = self.compression_combo.currentData()
        self.current_settings["max_main_text_chars"] = self.max_main_text_chars_spinbox.value()
        self.current_settings["token_compression_step_chars"] = self.token_compression_step_spinbox.value()

        # Save infinite generation behavior settings
        inf_gen_behavior = self.current_settings.get("infinite_generation_behavior", {})
        inf_gen_behavior["idea"] = "immediate" if self.idea_immediate_radio.isChecked() else "manual"
        inf_gen_behavior["generate"] = "immediate" if self.gen_immediate_radio.isChecked() else "manual"
        self.current_settings["infinite_generation_behavior"] = inf_gen_behavior

        # Save transfer settings
        if self.transfer_next_always_radio.isChecked():
            self.current_settings["transfer_to_main_mode"] = "next_line_always"
        elif self.transfer_next_eol_radio.isChecked():
            self.current_settings["transfer_to_main_mode"] = "next_line_eol"
        else:
            self.current_settings["transfer_to_main_mode"] = "cursor"
        self.current_settings["transfer_newlines_before"] = self.transfer_newlines_spinbox.value()

        # Save default rating setting
        self.current_settings["default_rating"] = self.rating_combo.currentData()
        self.current_settings["prompt_delivery_mode"] = self.prompt_delivery_combo.currentData()
        self.current_settings["system_prompt"] = self.system_prompt_edit.toPlainText()
        self.current_settings["thinking_template_preset"] = self.thinking_template_preset_combo.currentData()
        self.current_settings["prefill_thinking_strategy"] = self.prefill_thinking_strategy_combo.currentData()
        self.current_settings["prefill_thinking_custom_prefix"] = self.prefill_thinking_custom_prefix_edit.toPlainText()
        self.current_settings["prefill_thinking_custom_suffix"] = self.prefill_thinking_custom_suffix_edit.toPlainText()

        # Save Author's Note Display Mode setting
        self.current_settings["authors_note_display_mode"] = self.authors_note_combo.currentData()

        save_settings(self.current_settings)
        super().accept()

    @Slot()
    def _update_newline_spinbox_state(self):
        """Enables or disables the newline spinbox based on the selected transfer mode."""
        enable = self.transfer_next_always_radio.isChecked() or self.transfer_next_eol_radio.isChecked()
        self.transfer_newlines_spinbox.setEnabled(enable)

    @Slot()
    def _update_prefill_thinking_custom_state(self):
        use_custom = self.prefill_thinking_strategy_combo.currentData() == "custom"
        self.prefill_thinking_custom_prefix_edit.setEnabled(use_custom)
        self.prefill_thinking_custom_suffix_edit.setEnabled(use_custom)

    @Slot()
    def _sync_prefill_thinking_strategy_to_template(self):
        preset = self.thinking_template_preset_combo.currentData()
        if preset == "gemma4":
            strategy = "gemma4_channel"
            enabled = False
        else:
            strategy = "disabled"
            enabled = False

        index = self.prefill_thinking_strategy_combo.findData(strategy)
        if index != -1:
            self.prefill_thinking_strategy_combo.setCurrentIndex(index)
        self.prefill_thinking_strategy_combo.setEnabled(enabled)
        self.prefill_thinking_custom_prefix_edit.setEnabled(False)
        self.prefill_thinking_custom_suffix_edit.setEnabled(False)

    @staticmethod
    def show_dialog(parent: QWidget | None = None) -> bool:
       """Creates and shows the dialog, returning True if accepted."""
       dialog = GenerationParamsDialog(parent)
       return dialog.exec() == QDialog.Accepted

if __name__ == '__main__':
    # Example usage for testing dialogs individually
    from PySide6.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)

    # Test KoboldConfigDialog
    print("Showing Kobold Config Dialog...")
    if KoboldConfigDialog.show_dialog():
        print("Kobold Config Dialog Accepted. Settings potentially saved.")
        print("Current settings:", load_settings())
    else:
        print("Kobold Config Dialog Cancelled.")

    # Test GenerationParamsDialog
    print("\nShowing Generation Params Dialog...")
    if GenerationParamsDialog.show_dialog():
        print("Generation Params Dialog Accepted. Settings potentially saved.")
        print("Current settings:", load_settings())
    else:
        print("Generation Params Dialog Cancelled.")

    sys.exit(app.exec())
