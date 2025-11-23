from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
                               QDialogButtonBox, QWidget, QGroupBox, QRadioButton,
                               QFormLayout, QCheckBox)
from PySide6.QtCore import Slot
from src.core.settings import load_settings, save_settings, DEFAULT_SETTINGS

class AutocompleteSettingsDialog(QDialog):
    """Dialog for configuring autocomplete settings."""
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("リアルタイム提案（ベータ）設定")
        self.setMinimumWidth(400)

        self.current_settings = load_settings()

        main_layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        # 最大生成長
        self.max_length_spinbox = QSpinBox()
        self.max_length_spinbox.setRange(10, 200)
        self.max_length_spinbox.setValue(self.current_settings.get("max_length_autocomplete", DEFAULT_SETTINGS["max_length_autocomplete"]))
        form_layout.addRow("最大生成長:", self.max_length_spinbox)

        # 反応待ち時間（デバウンス時間）
        self.debounce_spinbox = QSpinBox()
        self.debounce_spinbox.setRange(100, 5000)
        self.debounce_spinbox.setSingleStep(100)
        self.debounce_spinbox.setValue(self.current_settings.get("autocomplete_debounce_ms", DEFAULT_SETTINGS["autocomplete_debounce_ms"]))
        form_layout.addRow("反応待ち時間 (ms):", self.debounce_spinbox)

        main_layout.addLayout(form_layout)

        # 改行抑制設定
        self.ban_newlines_checkbox = QCheckBox("改行を生成しない")
        self.ban_newlines_checkbox.setChecked(self.current_settings.get("autocomplete_ban_newlines", DEFAULT_SETTINGS["autocomplete_ban_newlines"]))
        main_layout.addWidget(self.ban_newlines_checkbox)

        # 動作モード設定
        mode_group = QGroupBox("動作モード")
        mode_layout = QVBoxLayout(mode_group)

        self.auto_radio = QRadioButton("自動 (Automatic): 入力停止後に自動生成")
        self.manual_radio = QRadioButton("手動 (Manual): Ctrl+Spaceを押した時のみ生成")
        
        mode_layout.addWidget(self.auto_radio)
        mode_layout.addWidget(self.manual_radio)

        # 初期状態の設定
        current_mode = self.current_settings.get("autocomplete_trigger_mode", DEFAULT_SETTINGS["autocomplete_trigger_mode"])
        if current_mode == "manual":
            self.manual_radio.setChecked(True)
        else:
            self.auto_radio.setChecked(True)

        main_layout.addWidget(mode_group)

        # ダイアログボタン
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def accept(self):
        """設定を保存してダイアログを閉じる"""
        self.current_settings["max_length_autocomplete"] = self.max_length_spinbox.value()
        self.current_settings["autocomplete_debounce_ms"] = self.debounce_spinbox.value()
        self.current_settings["autocomplete_ban_newlines"] = self.ban_newlines_checkbox.isChecked()
        
        # 動作モードの保存
        if self.manual_radio.isChecked():
            self.current_settings["autocomplete_trigger_mode"] = "manual"
        else:
            self.current_settings["autocomplete_trigger_mode"] = "auto"

        save_settings(self.current_settings)
        super().accept()

    @staticmethod
    def show_dialog(parent: QWidget | None = None) -> bool:
        """ダイアログを表示し、OKが押された場合Trueを返す"""
        dialog = AutocompleteSettingsDialog(parent)
        return dialog.exec() == QDialog.Accepted