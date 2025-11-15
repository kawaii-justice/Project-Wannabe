from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QCheckBox, QDialogButtonBox,
                               QTextEdit, QPlainTextEdit, QGroupBox, QComboBox, QMessageBox)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QTextCursor, QTextCharFormat, QColor
import re
from typing import Optional, List, Dict, Union

# 検索対象の定義
SEARCH_TARGETS = {
    "本文": "main_text",
    "メモ": "memo",
    "出力": "output",
    "詳細情報": {
        "タイトル": "title",
        "あらすじ": "synopsis",
        "設定": "setting",
        "プロット": "plot",
        "次の展開についてのメモ": "authors_note"
    }
}


class SearchDialog(QDialog):
    """検索と置換ダイアログ"""
    
    # シグナル定義
    search_next = Signal(str, bool, bool)  # 検索文字列, 大文字小文字区別, 正規表現
    search_previous = Signal(str, bool, bool)
    replace_one = Signal(str, str, bool, bool)  # 検索文字列, 置換文字列, 大文字小文字区別, 正規表現
    replace_all = Signal(str, str, bool, bool)
    target_changed = Signal(str)  # 検索対象変更シグナル
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("検索と置換(実験的)")
        self.setModal(False)  # モードレスダイアログ
        self.setFixedSize(450, 320)  # サイズを拡大して検索対象選択UIを収める
        
        # 検索履歴
        self.search_history: List[str] = []
        self.replace_history: List[str] = []
        
        # 検索対象
        self.current_target = "main_text"  # デフォルトは本文
        
        self._setup_ui()
        self._connect_signals()
        
    def _setup_ui(self):
        """UIのセットアップ"""
        layout = QVBoxLayout(self)
        
        # 検索対象選択
        target_layout = QHBoxLayout()
        target_layout.addWidget(QLabel("検索対象:"))
        
        # メイン検索対象選択
        self.main_target_combo = QComboBox()
        self.main_target_combo.addItems(list(SEARCH_TARGETS.keys()))
        self.main_target_combo.setCurrentText("本文")  # デフォルトは本文
        target_layout.addWidget(self.main_target_combo)
        
        # 詳細情報用のサブ選択（初期は非表示）
        self.sub_target_combo = QComboBox()
        self.sub_target_combo.setVisible(False)
        target_layout.addWidget(self.sub_target_combo)
        
        target_layout.addStretch()
        layout.addLayout(target_layout)
        
        # 検索文字列入力
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("検索文字列:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("検索する文字列を入力...")
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)
        
        # 置換文字列入力
        replace_layout = QHBoxLayout()
        replace_layout.addWidget(QLabel("置換文字列:"))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("置換する文字列を入力...")
        replace_layout.addWidget(self.replace_input)
        layout.addLayout(replace_layout)
        
        # オプション
        options_group = QGroupBox("オプション")
        options_layout = QVBoxLayout(options_group)
        
        self.case_sensitive_check = QCheckBox("大文字小文字を区別")
        self.regex_check = QCheckBox("正規表現")
        
        options_layout.addWidget(self.case_sensitive_check)
        options_layout.addWidget(self.regex_check)
        
        layout.addWidget(options_group)
        
        # ステータス表示
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(self.status_label)
        
        # ボタンレイアウト
        button_layout = QHBoxLayout()
        
        # 検索ボタン
        search_buttons_layout = QVBoxLayout()
        
        self.search_next_btn = QPushButton("次を検索")
        self.search_next_btn.setShortcut("F3")
        self.search_prev_btn = QPushButton("前を検索")
        self.search_prev_btn.setShortcut("Shift+F3")
        
        search_buttons_layout.addWidget(self.search_next_btn)
        search_buttons_layout.addWidget(self.search_prev_btn)
        button_layout.addLayout(search_buttons_layout)
        
        # 置換ボタン
        replace_buttons_layout = QVBoxLayout()
        
        self.replace_btn = QPushButton("置換")
        self.replace_all_btn = QPushButton("すべて置換")
        
        replace_buttons_layout.addWidget(self.replace_btn)
        replace_buttons_layout.addWidget(self.replace_all_btn)
        button_layout.addLayout(replace_buttons_layout)
        
        # キャンセルボタン
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
    def _connect_signals(self):
        """シグナルの接続"""
        # 検索対象選択
        self.main_target_combo.currentTextChanged.connect(self._on_main_target_changed)
        self.sub_target_combo.currentTextChanged.connect(self._on_sub_target_changed)
        
        # 検索ボタン
        self.search_next_btn.clicked.connect(self._on_search_next)
        self.search_prev_btn.clicked.connect(self._on_search_previous)
        
        # 置換ボタン
        self.replace_btn.clicked.connect(self._on_replace_one)
        self.replace_all_btn.clicked.connect(self._on_replace_all)
        
        # エンターキーで検索
        self.search_input.returnPressed.connect(self._on_search_next)
        
        # オプション変更時の即座反映
        self.case_sensitive_check.toggled.connect(self._on_search_options_changed)
        self.regex_check.toggled.connect(self._on_search_options_changed)
        
    def _on_search_next(self):
        """次を検索"""
        search_text = self.search_input.text()
        if not search_text:
            self._show_status("検索文字列を入力してください", error=True)
            return
            
        case_sensitive = self.case_sensitive_check.isChecked()
        use_regex = self.regex_check.isChecked()
        
        self.search_next.emit(search_text, case_sensitive, use_regex)
        
    def _on_search_previous(self):
        """前を検索"""
        search_text = self.search_input.text()
        if not search_text:
            self._show_status("検索文字列を入力してください", error=True)
            return
            
        case_sensitive = self.case_sensitive_check.isChecked()
        use_regex = self.regex_check.isChecked()
        
        self.search_previous.emit(search_text, case_sensitive, use_regex)
        
    def _on_replace_one(self):
        """1つ置換"""
        search_text = self.search_input.text()
        replace_text = self.replace_input.text()
        
        if not search_text:
            self._show_status("検索文字列を入力してください", error=True)
            return
            
        # 空文字列での置換は警告ダイアログを表示
        if replace_text == "":
            reply = QMessageBox.warning(
                self,
                "警告",
                f"検索文字列「{search_text}」を空文字列に置換しようとしています。\n"
                "この操作により、一致する文字列が削除されます。\n"
                "本当に続行しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                return
            
        case_sensitive = self.case_sensitive_check.isChecked()
        use_regex = self.regex_check.isChecked()
        
        self.replace_one.emit(search_text, replace_text, case_sensitive, use_regex)
        
    def _on_replace_all(self):
        """すべて置換"""
        search_text = self.search_input.text()
        replace_text = self.replace_input.text()
        
        if not search_text:
            self._show_status("検索文字列を入力してください", error=True)
            return
            
        # 空文字列での置換は特別な警告と確認
        if replace_text == "":
            reply = QMessageBox.warning(
                self,
                "警告",
                f"検索文字列「{search_text}」を空文字列に置換しようとしています。\n"
                "この操作により、一致するすべての文字列が削除されます。\n"
                "本当に続行しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                return
        
        # 空文字列でない場合でも、大量の置換には確認
        elif len(search_text) <= 1:
            reply = QMessageBox.question(
                self,
                "確認",
                f"検索文字列「{search_text}」を「{replace_text}」にすべて置換します。\n"
                "一致する文字列が多い場合、元に戻せません。\n"
                "本当に続行しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply != QMessageBox.Yes:
                return
            
        case_sensitive = self.case_sensitive_check.isChecked()
        use_regex = self.regex_check.isChecked()
        
        self.replace_all.emit(search_text, replace_text, case_sensitive, use_regex)
        
    def _on_search_options_changed(self):
        """検索オプションが変更された時"""
        # 現在の検索をリセットして新しいオプションで再検索
        if self.search_input.text():
            self._on_search_next()
            
    def _on_main_target_changed(self, target_name: str):
        """メイン検索対象が変更された時"""
        # 詳細情報が選択された場合はサブ選択を表示
        if target_name == "詳細情報":
            sub_targets = list(SEARCH_TARGETS["詳細情報"].keys())
            self.sub_target_combo.clear()
            self.sub_target_combo.addItems(sub_targets)
            self.sub_target_combo.setVisible(True)
            self.current_target = SEARCH_TARGETS["詳細情報"][sub_targets[0]]
        else:
            self.sub_target_combo.setVisible(False)
            self.current_target = SEARCH_TARGETS[target_name]
        
        # 検索対象変更を通知
        self.target_changed.emit(self.current_target)
        
        # 現在の検索をリセット
        if self.search_input.text():
            self._on_search_next()
    
    def _on_sub_target_changed(self, sub_target_name: str):
        """サブ検索対象が変更された時"""
        if self.main_target_combo.currentText() == "詳細情報":
            self.current_target = SEARCH_TARGETS["詳細情報"][sub_target_name]
            # 検索対象変更を通知
            self.target_changed.emit(self.current_target)
            
            # 現在の検索をリセット
            if self.search_input.text():
                self._on_search_next()
    
    def get_selected_target(self) -> str:
        """現在選択されている検索対象を返す"""
        return self.current_target
            
    def _show_status(self, message: str, error: bool = False):
        """ステータス表示"""
        if error:
            self.status_label.setStyleSheet("color: #d32f2f; font-size: 10px;")
        else:
            self.status_label.setStyleSheet("color: #666; font-size: 10px;")
            
        self.status_label.setText(message)
        
        # エラーメッセージは3秒後にクリア
        if error:
            QTimer.singleShot(3000, lambda: self.status_label.clear())
            
    def showEvent(self, event):
        """ダイアログ表示時"""
        super().showEvent(event)
        self.search_input.setFocus()
        self.search_input.selectAll()
        
    def keyPressEvent(self, event):
        """キー押下処理"""
        if event.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class SearchManager:
    """検索と置換のロジックを管理するクラス"""
    
    def __init__(self):
        self.current_text_widget: Optional[QPlainTextEdit] = None
        self.current_line_edit: Optional[QLineEdit] = None
        self.search_results: List[tuple] = []  # (start, length) のリスト
        self.current_result_index: int = -1
        self.last_search_pattern: str = ""
        
        # ハイライトフォーマット
        self.highlight_format = QTextCharFormat()
        self.highlight_format.setBackground(QColor(255, 255, 0))  # 黄色
        
        self.current_highlight_format = QTextCharFormat()
        self.current_highlight_format.setBackground(QColor(255, 165, 0))  # オレンジ
        
    def set_text_widget(self, text_widget):
        """検索対象のテキストウィジェットを設定（QPlainTextEditまたはQLineEdit）"""
        current_widget = self.current_text_widget or self.current_line_edit
        if current_widget is text_widget:
            return  # 同じウィジェットなら何もしない

        # ウィジェットが変更されたので、古いウィジェットのハイライトを消し、検索状態をリセット
        self.clear_highlights()
        self.search_results.clear()
        self.current_result_index = -1
        self.last_search_pattern = ""

        if isinstance(text_widget, QPlainTextEdit):
            self.current_text_widget = text_widget
            self.current_line_edit = None
        elif isinstance(text_widget, QLineEdit):
            self.current_line_edit = text_widget
            self.current_text_widget = None
        else:
            self.current_text_widget = None
            self.current_line_edit = None
        
    def clear_highlights(self):
        """ハイライトをクリア"""
        if self.current_text_widget:
            cursor = self.current_text_widget.textCursor()
            cursor.select(QTextCursor.Document)
            cursor.setCharFormat(QTextCharFormat())  # デフォルトフォーマットに戻す
            cursor.clearSelection()
        elif self.current_line_edit:
            # QLineEditの場合はハイライトをクリア（選択を解除）
            self.current_line_edit.setSelection(0, 0)
        # ここでは検索結果リストはクリアしない
        
    def find_text(self, pattern: str, case_sensitive: bool = False, 
                  use_regex: bool = False, forward: bool = True) -> bool:
        """テキストを検索"""
        if not pattern:
            return False
            
        # ウィジェットタイプに応じてテキストを取得
        if self.current_text_widget:
            text = self.current_text_widget.toPlainText()
            if not text:
                return False
        elif self.current_line_edit:
            text = self.current_line_edit.text()
            if not text:
                return False
        else:
            return False
            
        # 新しい検索が始まったので、以前のハイライトと結果をクリア
        if self.last_search_pattern != pattern:
            self.clear_highlights()
            self.search_results = []
            self.current_result_index = -1

        try:
            # 正規表現のコンパイル
            if use_regex:
                flags = 0 if case_sensitive else re.IGNORECASE
                regex = re.compile(pattern, flags)
            else:
                # 通常の文字列検索
                if case_sensitive:
                    pattern = re.escape(pattern)
                else:
                    pattern = re.escape(pattern)
                    text = text.lower()
                    pattern = pattern.lower()
                regex = re.compile(pattern)
                
            # すべてのマッチを検索
            if not self.search_results:
                for match in regex.finditer(text):
                    self.search_results.append((match.start(), match.end() - match.start()))

            if not self.search_results:
                self.clear_highlights()
                return False
                
            # 現在のカーソル位置を取得
            current_pos = 0
            if self.current_text_widget:
                cursor = self.current_text_widget.textCursor()
                # 前を検索する場合は選択範囲の開始位置、次を検索する場合は終了位置を基準にする
                current_pos = cursor.selectionStart() if not forward else cursor.position()
            elif self.current_line_edit:
                # QLineEditも同様
                current_pos = self.current_line_edit.selectionStart() if not forward else (self.current_line_edit.selectionStart() + self.current_line_edit.selectionLength())
            
            # 次のマッチを探す
            if forward:
                self.current_result_index = self._find_next_match_index(current_pos)
            else:
                self.current_result_index = self._find_previous_match_index(current_pos)
                
            if self.current_result_index == -1:
                # 見つからない場合は先頭/末尾から再検索
                if forward:
                    self.current_result_index = 0
                else:
                    self.current_result_index = len(self.search_results) - 1
                    
            # ハイライトとカーソル移動
            self._highlight_and_move_to_current()
            self.last_search_pattern = pattern
            
            return True
            
        except re.error as e:
            # 正規表現エラー
            return False
            
    def _find_next_match_index(self, current_pos: int) -> int:
        """次のマッチのインデックスを探す"""
        for i, (start, length) in enumerate(self.search_results):
            if start > current_pos:
                return i
        return -1
        
    def _find_previous_match_index(self, current_pos: int) -> int:
        """前のマッチのインデックスを探す"""
        # 比較対象は、選択範囲の開始位置にする
        pos_to_compare = current_pos
        if self.current_text_widget:
            pos_to_compare = self.current_text_widget.textCursor().selectionStart()
        elif self.current_line_edit:
            pos_to_compare = self.current_line_edit.selectionStart()

        for i in range(len(self.search_results) - 1, -1, -1):
            start, length = self.search_results[i]
            if start < pos_to_compare:
                return i
        return -1
        
    def _highlight_and_move_to_current(self):
        """現在のマッチをハイライトして移動"""
        if self.current_result_index == -1:
            return

        # すべてのマッチをハイライトしなおす（現在のマッチは別の色で）
        self._apply_highlights()
        
        # 現在のマッチに移動
        start, length = self.search_results[self.current_result_index]
        
        if self.current_text_widget:
            cursor = self.current_text_widget.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.KeepAnchor)
            
            # 現在のマッチを特別にハイライト
            cursor.setCharFormat(self.current_highlight_format)
            
            # ビューにスクロール
            self.current_text_widget.setTextCursor(cursor)
            self.current_text_widget.ensureCursorVisible()
        elif self.current_line_edit:
            # QLineEditの場合は選択でハイライト
            self.current_line_edit.setSelection(start, length)
        
    def _apply_highlights(self):
        """すべてのマッチをハイライト"""
        self.clear_highlights()  # 既存のハイライトを一旦すべてクリア
        
        # QPlainTextEditの場合のみハイライト（QLineEditは複数ハイライト非対応）
        if self.current_text_widget:
            # すべてのマッチをハイライト（現在のマッチは後で上書き）
            for i, (start, length) in enumerate(self.search_results):
                if i != self.current_result_index:  # 現在のマッチは後で処理
                    cursor = self.current_text_widget.textCursor()
                    cursor.setPosition(start)
                    cursor.setPosition(start + length, QTextCursor.KeepAnchor)
                    cursor.setCharFormat(self.highlight_format)
                
    def find_next(self, pattern: str, case_sensitive: bool = False, 
                  use_regex: bool = False) -> bool:
        """次を検索"""
        if pattern != self.last_search_pattern or not self.search_results:
            # 新しいパターンで検索
            return self.find_text(pattern, case_sensitive, use_regex, forward=True)
        else:
            # 既存の結果から次へ
            if not self.search_results:
                return False
                
            self.current_result_index = (self.current_result_index + 1) % len(self.search_results)
            self._highlight_and_move_to_current()
            return True
            
    def find_previous(self, pattern: str, case_sensitive: bool = False,
                       use_regex: bool = False) -> bool:
        """前を検索"""
        if pattern != self.last_search_pattern or not self.search_results:
            # 新しいパターンで検索
            return self.find_text(pattern, case_sensitive, use_regex, forward=False)
        else:
            # 既存の結果から前へ
            if not self.search_results:
                return False
                
            self.current_result_index = (self.current_result_index - 1) % len(self.search_results)
            self._highlight_and_move_to_current()
            return True
            
    def replace_current(self, search_pattern: str, replace_text: str,
                       case_sensitive: bool = False, use_regex: bool = False) -> bool:
        """現在のマッチを置換"""
        if self.current_result_index == -1:
            return False
            
        try:
            start, length = self.search_results[self.current_result_index]
            
            if self.current_text_widget:
                cursor = self.current_text_widget.textCursor()
                cursor.setPosition(start)
                cursor.setPosition(start + length, QTextCursor.KeepAnchor)
                
                # 置換実行
                cursor.insertText(replace_text)
                
                # 検索結果を更新
                self.find_text(search_pattern, case_sensitive, use_regex, forward=True)
                return True
            elif self.current_line_edit:
                # QLineEditの置換
                text = self.current_line_edit.text()
                new_text = text[:start] + replace_text + text[start + length:]
                self.current_line_edit.setText(new_text)
                
                # 検索結果を更新
                self.find_text(search_pattern, case_sensitive, use_regex, forward=True)
                return True
            
        except Exception:
            return False
        return False
            
    def replace_all(self, search_pattern: str, replace_text: str,
                   case_sensitive: bool = False, use_regex: bool = False) -> int:
        """すべて置換"""
        try:
            if self.current_text_widget:
                text = self.current_text_widget.toPlainText()
                
                if use_regex:
                    flags = 0 if case_sensitive else re.IGNORECASE
                    new_text, count = re.subn(search_pattern, replace_text, text, flags=flags)
                else:
                    if case_sensitive:
                        new_text = text.replace(search_pattern, replace_text)
                        count = text.count(search_pattern)
                    else:
                        # 大文字小文字無視の置換
                        pattern = re.compile(re.escape(search_pattern), re.IGNORECASE)
                        new_text, count = pattern.subn(replace_text, text)
                         
                if count > 0:
                    self.current_text_widget.setPlainText(new_text)
                    self.clear_highlights()
                    
                return count
            elif self.current_line_edit:
                # QLineEditの置換
                text = self.current_line_edit.text()
                
                if use_regex:
                    flags = 0 if case_sensitive else re.IGNORECASE
                    new_text, count = re.subn(search_pattern, replace_text, text, flags=flags)
                else:
                    if case_sensitive:
                        new_text = text.replace(search_pattern, replace_text)
                        count = text.count(search_pattern)
                    else:
                        # 大文字小文字無視の置換
                        pattern = re.compile(re.escape(search_pattern), re.IGNORECASE)
                        new_text, count = pattern.subn(replace_text, text)
                         
                if count > 0:
                    self.current_line_edit.setText(new_text)
                    self.clear_highlights()
                    
                return count
                
        except Exception:
            return 0
        return 0
            
    def get_search_info(self) -> tuple:
        """検索情報を取得（進捗表示用）"""
        if not self.search_results:
            return (0, 0)
        return (self.current_result_index + 1, len(self.search_results))
