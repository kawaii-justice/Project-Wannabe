import asyncio
from typing import Optional
from PySide6.QtCore import QTimer, QObject, QEvent, Qt
from PySide6.QtGui import QTextCursor, QTextCharFormat, QColor, QKeyEvent, QPalette
from PySide6.QtWidgets import QPlainTextEdit, QApplication

from src.core.kobold_client import KoboldClient, KoboldClientError
from src.core.settings import load_settings, DEFAULT_SETTINGS
from src.core.prompt_builder import build_prompt, build_prompt_with_compression
from src.core.dynamic_prompts import evaluate_dynamic_prompt, is_position_valid


class AutocompleteManager(QObject):
    """
    オートコンプリート機能を管理するクラス
    
    このクラスは以下の責務を持つ：
    1. デバウンス処理（入力監視）
    2. テキスト処理とSuffixの分離
    3. 生成リクエスト
    4. 結果の確認
    """
    
    def __init__(self, main_text_edit: QPlainTextEdit, kobold_client: KoboldClient):
        """
        AutocompleteManagerを初期化する
        
        Args:
            main_text_edit: 本文入力用のQPlainTextEdit
            kobold_client: KoboldClientインスタンス
        """
        super().__init__()
        self.main_text_edit = main_text_edit
        self.kobold_client = kobold_client
        
        # 設定の読み込み
        self.settings = load_settings()
        self.is_enabled = False  # デフォルトで無効
        self.debounce_ms = self.settings.get("autocomplete_debounce_ms", DEFAULT_SETTINGS["autocomplete_debounce_ms"])
        self.max_length = self.settings.get("max_length_autocomplete", DEFAULT_SETTINGS["max_length_autocomplete"])
        self.trigger_mode = self.settings.get("autocomplete_trigger_mode", DEFAULT_SETTINGS["autocomplete_trigger_mode"])  # トリガーモード
        
        # デバウンスタイマーの設定
        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self._on_debounce_timer_timeout)
        
        # 現在の生成タスク
        self.current_generation_task: Optional[asyncio.Task] = None
        
        # ゴーストテキスト管理
        self.ghost_text_cursor: Optional[QTextCursor] = None  # ゴーストテキストの範囲を保持
        self._ghost_insert_revision: Optional[int] = None  # Undo安全判定用（ゴースト挿入直後のdocument revision）
        self._is_showing_ghost_text = False  # ゴーストテキスト表示中フラグ
        self.ghost_text_format = QTextCharFormat()
        # テーマに対応した色を設定（初期値として設定し、show_ghost_textで毎回取得）
        self._update_ghost_text_format()
        self.normal_text_format = QTextCharFormat()
        # 色指定を解除するためのフォーマット
        self.normal_text_format.clearProperty(QTextCharFormat.ForegroundBrush)
        
        # シグナルの接続
        self.main_text_edit.textChanged.connect(self._on_text_changed)
        self.main_text_edit.cursorPositionChanged.connect(self._on_cursor_position_changed)
        self.main_text_edit.installEventFilter(self)
        
        print("[AutocompleteManager] Initialization complete")

    def eventFilter(self, watched, event):
        """
        マウス操作（右クリック→貼り付け等）ではUndoスタックのトップがゴースト挿入とは
        限らないため、ゴーストはremoveSelectedTextで確実に消す。
        """
        if watched is self.main_text_edit and self.has_ghost_text():
            et = event.type()
            if et == QEvent.ContextMenu:
                self.clear_ghost_text(prefer_undo=False)
            elif et == QEvent.MouseButtonPress and hasattr(event, "button") and event.button() == Qt.RightButton:
                self.clear_ghost_text(prefer_undo=False)
            elif et in (QEvent.DragEnter, QEvent.Drop):
                self.clear_ghost_text(prefer_undo=False)

        return super().eventFilter(watched, event)

    def _can_undo_ghost_insertion(self) -> bool:
        if self.ghost_text_cursor is None or self._ghost_insert_revision is None:
            return False
        doc = self.main_text_edit.document()
        if not doc.isUndoAvailable():
            return False
        return doc.revision() == self._ghost_insert_revision
    
    def _on_text_changed(self):
        """テキストが変更された時の処理"""
        if not self.is_enabled:
            return
        
        # マニュアルモードの場合は自動生成しない
        if self.trigger_mode == "manual":
            return
        
        # ゴーストテキスト表示中の変更は無視（自分で挿入したテキストによるイベントを防ぐ）
        if self._is_showing_ghost_text:
            return
            
        # デバウンスタイマーをリセット
        self.debounce_timer.stop()
        self.debounce_timer.start(self.debounce_ms)
        print(f"[AutocompleteManager] Text change detected - debounce timer started ({self.debounce_ms}ms)")
    
    def _on_cursor_position_changed(self):
        """カーソル位置が変更された時の処理"""
        if not self.is_enabled:
            return
        
        # マニュアルモードの場合は自動生成しない
        if self.trigger_mode == "manual":
            return
        
        # ゴーストテキスト表示中のカーソル位置変更は無視（自分で挿入したテキストによるイベントを防ぐ）
        if self._is_showing_ghost_text:
            return
        
        # ゴーストテキストが表示中ならクリア
        self.handle_cursor_movement()
            
        # デバウンスタイマーをリセット
        self.debounce_timer.stop()
        self.debounce_timer.start(self.debounce_ms)
        print(f"[AutocompleteManager] Cursor position change detected - debounce timer started ({self.debounce_ms}ms)")
    
    def _on_debounce_timer_timeout(self):
        """デバウンスタイマーがタイムアウトした時の処理"""
        print("[AutocompleteManager] Debounce timer expired - starting autocomplete generation")
        
        # 非同期タスクとして実行
        asyncio.ensure_future(self._generate_autocomplete_async())
    
    async def _generate_autocomplete_async(self):
        """オートコンプリート生成の非同期処理"""
        try:
            # 現在の生成タスクをキャンセル
            if self.current_generation_task and not self.current_generation_task.done():
                self.current_generation_task.cancel()
                print("[AutocompleteManager] 既存の生成タスクをキャンセル")
            
            # カーソル位置までのテキストを取得
            cursor = self.main_text_edit.textCursor()
            cursor_position = cursor.position()
            full_text = self.main_text_edit.toPlainText()
            if not is_position_valid(full_text, cursor_position):
                print("[AutocompleteManager] Skipping - cursor position is outside valid range")
                return
            text_up_to_cursor = full_text[:cursor_position]
            text_up_to_cursor = evaluate_dynamic_prompt(text_up_to_cursor)
            
            if not text_up_to_cursor.strip():
                print("[AutocompleteManager] Skipping - text is empty after filtering")
                return
            
            # 生成開始前のカーソル位置を保存（生成中にカーソルが移動したかを判定するため）
            start_cursor_pos = cursor.position()
            
            # メインウィンドウからUIデータを取得
            # main_windowへの参照を保持していないため、親ウィンドウをたどって取得
            main_window = self.main_text_edit.parent()
            while main_window and not hasattr(main_window, '_get_metadata_from_ui'):
                main_window = main_window.parent()
            
            if not main_window:
                print("[AutocompleteManager] Error: Could not find main window")
                return
            
            ui_data = main_window._get_metadata_from_ui()
            
            # build_prompt_with_compressionを使用してプロンプトを構築（動的圧縮対応）
            prompt, total_tokens, is_overflow, original_body_chars, compressed_body_chars = await build_prompt_with_compression(
                base_url=self.kobold_client._get_api_base_url(),
                current_mode="autocomplete",
                main_text=text_up_to_cursor,
                ui_data=ui_data,
                compression_mode=self.settings.get("compression_mode", "token_dynamic"),
                max_length_generate=self.max_length  # オートコンプリート用の最大長を指定
            )
            
            # オーバーフロー判定: 圧縮後もコンテキスト長を超える場合は生成をスキップ
            if is_overflow:
                print(f"[AutocompleteManager] Context overflow detected. Skipping generation. Total tokens: {total_tokens}")
                return
            
            # 圧縮が行われた場合のログ出力
            if compressed_body_chars and original_body_chars and compressed_body_chars < original_body_chars:
                print(f"[AutocompleteManager] Text compressed: {original_body_chars} -> {compressed_body_chars} chars")
            
            print(f"[AutocompleteManager] Generated prompt for autocomplete ({total_tokens} tokens)")
            
            # 生成を実行
            print(f"[AutocompleteManager] Starting generation - max_length: {self.max_length}")
            
            # 設定の禁止ワードを取得してコピー
            banned_strings_list = self.settings.get("banned_tokens", []).copy()
            
            # 改行禁止設定がオンの場合、改行を追加（上書きではなくマージ）
            ban_newlines = self.settings.get("autocomplete_ban_newlines", False)
            if ban_newlines:
                if "\n" not in banned_strings_list:
                    banned_strings_list.append("\n")
                print(f"[AutocompleteManager] Banning newlines in generation (added to banned tokens)")
            
            # banned_strings_listが空でない場合のみ渡す
            banned_strings = banned_strings_list if banned_strings_list else None
            
            if banned_strings:
                print(f"[AutocompleteManager] Using banned tokens: {banned_strings}")
            
            generated_text = ""
            
            async for token in self.kobold_client.generate_stream(
                prompt,
                max_length=self.max_length,
                stop_sequence=None,  # 設定のストップシーケンスを使用
                banned_strings=banned_strings,  # マージされた禁止ワードリスト
                current_mode="autocomplete"
            ):
                generated_text += token
            
            # ゴーストテキストを表示（無効化時の表示残留バグ防止）
            if generated_text:
                # 生成完了後にカーソル位置をチェック（生成中にカーソルが移動したかを判定）
                current_cursor_pos = self.main_text_edit.textCursor().position()
                if start_cursor_pos != current_cursor_pos:
                    print("[AutocompleteManager] Cursor moved during generation, skipping display.")
                    return
                
                # 機能が無効化されている場合は表示しない
                if not self.is_enabled:
                    print("[AutocompleteManager] Skipping ghost text display - autocomplete disabled during generation")
                    return
                if main_window and hasattr(main_window, 'generation_status'):
                    if main_window.generation_status != "idle":
                        print(f"[AutocompleteManager] Skipping display - Main generation status is '{main_window.generation_status}'")
                        return
                print(f"[Autocomplete Suggestion]: {generated_text}")
                self.show_ghost_text(generated_text)

                
            else:
                print("[AutocompleteManager] Generation result was empty")
                
        except asyncio.CancelledError:
            print("[AutocompleteManager] Generation task was cancelled")
        except KoboldClientError as e:
            print(f"[AutocompleteManager] KoboldClient error: {e}")
        except Exception as e:
            print(f"[AutocompleteManager] Unexpected error: {e}")
    
    def _update_ghost_text_format(self):
        """ゴーストテキストのフォーマットをテーマに合わせて更新"""
        # QApplication.palette().color(QPalette.PlaceholderText)を使用してテーマ対応
        placeholder_color = QApplication.palette().color(QPalette.PlaceholderText)
        self.ghost_text_format.setForeground(placeholder_color)
    
    def reload_settings(self):
        """設定を再読み込みする"""
        self.settings = load_settings()
        self.debounce_ms = self.settings.get("autocomplete_debounce_ms", DEFAULT_SETTINGS["autocomplete_debounce_ms"])
        self.max_length = self.settings.get("max_length_autocomplete", DEFAULT_SETTINGS["max_length_autocomplete"])
        self.trigger_mode = self.settings.get("autocomplete_trigger_mode", DEFAULT_SETTINGS["autocomplete_trigger_mode"])
        print(f"[AutocompleteManager] Settings reloaded - debounce: {self.debounce_ms}ms, max_length: {self.max_length}, mode: {self.trigger_mode}, ban_newlines: {self.settings.get('autocomplete_ban_newlines', False)}")
    
    def set_enabled(self, enabled: bool):
        """
        オートコンプリート機能の有効/無効を設定する
        
        Args:
            enabled: Trueで有効、Falseで無効
        """
        self.is_enabled = enabled
        if not enabled:
            # 無効化時はタイマーを停止
            self.debounce_timer.stop()
            # 現在の生成タスクをキャンセル
            if self.current_generation_task and not self.current_generation_task.done():
                self.current_generation_task.cancel()
            # ゴーストテキストをクリア
            self.clear_ghost_text()
        print(f"[AutocompleteManager] Enabled state: {enabled}")
    
    def cleanup(self):
        """リソースのクリーンアップ"""
        self.debounce_timer.stop()
        if self.current_generation_task and not self.current_generation_task.done():
            self.current_generation_task.cancel()
        self.clear_ghost_text()
        print("[AutocompleteManager] Cleanup complete")
    
    def show_ghost_text(self, text: str):
        """
        ゴーストテキストを表示する
        
        Args:
            text: 表示するテキスト
        """
        # 既存のゴーストテキストをクリア
        self.clear_ghost_text()
        
        if not text:
            return
        
        # ゴーストテキスト表示中フラグをセット
        self._is_showing_ghost_text = True
        
        # テーマに対応した色を毎回取得
        self._update_ghost_text_format()
        
        # 現在のカーソル位置を取得
        cursor = self.main_text_edit.textCursor()
        insert_position = cursor.position()
        
        # テキストを挿入（挿入+色指定を1つのUndo操作にする）
        cursor.beginEditBlock()
        cursor.insertText(text, self.ghost_text_format)
        cursor.endEditBlock()
        end_position = cursor.position()
        self._ghost_insert_revision = self.main_text_edit.document().revision()
        
        # ゴーストテキストの範囲を保持
        cursor.setPosition(insert_position)
        cursor.setPosition(end_position, QTextCursor.MoveMode.KeepAnchor)
        self.ghost_text_cursor = QTextCursor(cursor)
        self.ghost_text_cursor.setPosition(insert_position)
        self.ghost_text_cursor.setPosition(end_position, QTextCursor.MoveMode.KeepAnchor)
        
        # カーソルを元の位置に戻す
        cursor.setPosition(insert_position)
        self.main_text_edit.setTextCursor(cursor)
        
        # ゴーストテキスト表示中フラグを解除
        self._is_showing_ghost_text = False
        
        print(f"[AutocompleteManager] Ghost text displayed: '{text[:50]}...'")
    
    def clear_ghost_text(self, restart_timer: bool = False, *, prefer_undo: bool = False):
        """
        ゴーストテキストをクリアする
        
        Args:
            restart_timer: クリア後にデバウンスタイマーを再始動するかどうか
        """
        if self.ghost_text_cursor is None:
            return
        
        # ゴーストテキスト表示中フラグをセット
        self._is_showing_ghost_text = True
        
        # ゴーストテキストを削除
        cursor = QTextCursor(self.ghost_text_cursor)
        start_position = cursor.selectionStart()
        if prefer_undo and self._can_undo_ghost_insertion():
            # キーボード入力直前など「Undoトップがゴースト挿入」と保証できる場合のみUndoで消す
            self.main_text_edit.undo()
            cursor = self.main_text_edit.textCursor()
            cursor.setPosition(start_position)
            self.main_text_edit.setTextCursor(cursor)
        else:
            # それ以外（マウス操作・貼り付け等）はUndoのトップが別操作の可能性があるため、直接削除する
            cursor.beginEditBlock()
            cursor.removeSelectedText()
            cursor.endEditBlock()
            cursor.setPosition(start_position)
            self.main_text_edit.setTextCursor(cursor)
        
        # 保持していた範囲をクリア
        self.ghost_text_cursor = None
        self._ghost_insert_revision = None
        
        # ゴーストテキスト表示中フラグを解除
        self._is_showing_ghost_text = False
        
        print("[AutocompleteManager] Ghost text cleared")
        
        # restart_timerがTrueの場合、デバウンスタイマーを再始動して連続生成を確保
        # ただし、マニュアルモードの場合は再始動しない
        if restart_timer and self.is_enabled and self.trigger_mode == "auto":
            self.debounce_timer.start(self.debounce_ms)
            print(f"[AutocompleteManager] Debounce timer restarted after clear ({self.debounce_ms}ms)")
    
    def commit_ghost_text(self) -> bool:
        """
        ゴーストテキストを確定する
        
        Returns:
            確定成功した場合True
        """
        if self.ghost_text_cursor is None:
            return False
        
        # ゴーストテキスト確定中フラグをセット
        self._is_showing_ghost_text = True
        
        cursor = QTextCursor(self.ghost_text_cursor)
        start_position = cursor.selectionStart()

        if self._can_undo_ghost_insertion():
            # Undoスタック上で「確定」だけが取り消されて灰色に戻るのを防ぐため、
            # いったんゴースト挿入をUndoし、通常のテキストとして挿入し直す。
            ghost_text = cursor.selectedText().replace("\u2029", "\n")
            self.main_text_edit.undo()

            # 同じ位置に通常テキストとして挿入（この挿入が1回のUndoで消える）
            cursor = self.main_text_edit.textCursor()
            cursor.setPosition(start_position)
            cursor.beginEditBlock()
            cursor.insertText(ghost_text)
            cursor.endEditBlock()
            self.main_text_edit.setTextCursor(cursor)
        else:
            # 例外ケースでは従来通り「色解除」で確定（誤Undoで別操作を消さない）
            cursor.setCharFormat(self.normal_text_format)
            end_position = cursor.selectionEnd()
            cursor.clearSelection()
            cursor.setPosition(end_position)
            self.main_text_edit.setTextCursor(cursor)
        
        # 保持していた範囲をクリア
        self.ghost_text_cursor = None
        self._ghost_insert_revision = None
        
        # ゴーストテキスト確定中フラグを解除
        self._is_showing_ghost_text = False
        
        print("[AutocompleteManager] Ghost text committed")
        
        # 確定後にデバウンスタイマーを再始動して連続生成を確保
        # ただし、マニュアルモードの場合は再始動しない
        if self.is_enabled and self.trigger_mode == "auto":
            self.debounce_timer.start(self.debounce_ms)
            print(f"[AutocompleteManager] Debounce timer restarted after commit ({self.debounce_ms}ms)")
        
        return True
    
    def has_ghost_text(self) -> bool:
        """
        ゴーストテキストが表示中かどうか
        
        Returns:
            表示中の場合True
        """
        return self.ghost_text_cursor is not None
    
    def handle_key_press(self, event: QKeyEvent) -> bool:
        """
        キー押下イベントを処理する
        
        Args:
            event: キーイベント
            
        Returns:
            イベントを処理した場合True（親への伝播を停止）
        """
        if not self.is_enabled:
            return False
        
        key = event.key()
        
        # Tabキー：ゴーストテキストを確定
        if key == Qt.Key_Tab:
            if self.has_ghost_text():
                self.commit_ghost_text()
                return True  # Tabのデフォルト動作を無効化
        
        # Escキー：ゴーストテキストをクリア（タイマー再始動）
        elif key == Qt.Key_Escape:
            if self.has_ghost_text():
                self.clear_ghost_text(restart_timer=True, prefer_undo=True)
                return True
        
        # 修飾キー単体の場合は何もしない（Ctrl、Shift、Altなど）
        elif key in [Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta]:
            return False
        
        # その他の通常キー：ゴーストテキストをクリアして通常の入力を許可
        else:
            if self.has_ghost_text():
                self.clear_ghost_text(prefer_undo=True)
        
        return False
    
    def handle_cursor_movement(self) -> bool:
        """
        カーソル移動イベントを処理する
        
        Returns:
            イベントを処理した場合True
        """
        if not self.is_enabled:
            return False
        
        if self.has_ghost_text():
            self.clear_ghost_text()
            return True
        
        return False
    
    def trigger_now(self):
        """
        手動でオートコンプリート生成を即座に開始する
        """
        if not self.is_enabled:
            print("[AutocompleteManager] Cannot trigger - autocomplete is disabled")
            return
        
        # 現在の生成タスクをキャンセル
        if self.current_generation_task and not self.current_generation_task.done():
            self.current_generation_task.cancel()
            print("[AutocompleteManager] Existing generation task cancelled for manual trigger")
        
        # 既存のゴーストテキストをクリア
        self.clear_ghost_text()
        
        # 即座に生成を開始
        print("[AutocompleteManager] Manual trigger - starting autocomplete generation immediately")
        asyncio.ensure_future(self._generate_autocomplete_async())
