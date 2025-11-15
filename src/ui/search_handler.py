from PySide6.QtWidgets import QPlainTextEdit, QLineEdit, QTextEdit, QMessageBox, QApplication
from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QKeySequence, QAction
from typing import Optional, Union

from src.ui.search_dialog import SearchDialog, SearchManager


class SearchHandler(QObject):
    """検索機能のハンドラ - メニューとの統合を担当"""
    
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.search_dialog: Optional[SearchDialog] = None
        self.search_manager = SearchManager()
        
        # 非同期処理用のタイマー
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._perform_search_async)
        
        self._pending_search_params = None
        
        # 検索対象のマッピング
        self.target_widgets = {}
        self._setup_target_mapping()
        
    def _setup_target_mapping(self):
        """検索対象のマッピングを設定"""
        if hasattr(self.main_window, 'main_text_edit'):
            self.target_widgets["main_text"] = self.main_window.main_text_edit
        if hasattr(self.main_window, 'memo_edit'):
            self.target_widgets["memo"] = self.main_window.memo_edit
        if hasattr(self.main_window, 'output_text_edit'):
            self.target_widgets["output"] = self.main_window.output_text_edit
        if hasattr(self.main_window, 'title_edit'):
            self.target_widgets["title"] = self.main_window.title_edit
        if hasattr(self.main_window, 'synopsis_edit'):
            self.target_widgets["synopsis"] = self.main_window.synopsis_edit
        if hasattr(self.main_window, 'setting_edit'):
            self.target_widgets["setting"] = self.main_window.setting_edit
        if hasattr(self.main_window, 'plot_edit'):
            self.target_widgets["plot"] = self.main_window.plot_edit
        if hasattr(self.main_window, 'authors_note_edit'):
            self.target_widgets["authors_note"] = self.main_window.authors_note_edit
    
    def create_search_actions(self):
        """検索関連のアクションを作成"""
        actions = []
        
        # 検索ダイアログ表示
        search_action = QAction("検索...", self.main_window)
        search_action.setShortcut("Ctrl+F")
        search_action.triggered.connect(self.show_search_dialog)
        actions.append(search_action)
        
        # 次を検索
        find_next_action = QAction("次を検索", self.main_window)
        find_next_action.setShortcut("F3")
        find_next_action.triggered.connect(self.find_next)
        actions.append(find_next_action)
        
        # 前を検索
        find_prev_action = QAction("前を検索", self.main_window)
        find_prev_action.setShortcut("Shift+F3")
        find_prev_action.triggered.connect(self.find_previous)
        actions.append(find_prev_action)
        
        return actions
        
    def show_search_dialog(self):
        """検索ダイアログを表示"""
        if not self.search_dialog:
            self.search_dialog = SearchDialog(self.main_window)
            # シグナルの接続
            self.search_dialog.search_next.connect(self._on_search_next)
            self.search_dialog.search_previous.connect(self._on_search_previous)
            self.search_dialog.replace_one.connect(self._on_replace_one)
            self.search_dialog.replace_all.connect(self._on_replace_all)
            self.search_dialog.target_changed.connect(self._on_target_changed)
            # ダイアログが閉じられたらハイライトをクリア
            self.search_dialog.finished.connect(self.hide_search_dialog)
            self.search_dialog.rejected.connect(self.hide_search_dialog)
            
            # 初期検索対象を設定
            initial_target = self.search_dialog.get_selected_target()
            self._set_search_target(initial_target)
            
        self.search_dialog.show()
        self.search_dialog.raise_()
        self.search_dialog.activateWindow()
            
    def find_next(self):
        """次を検索（F3）"""
        if not self.search_dialog or not self.search_dialog.isVisible():
            self.show_search_dialog()
            return
            
        search_text = self.search_dialog.search_input.text()
        if search_text:
            case_sensitive = self.search_dialog.case_sensitive_check.isChecked()
            use_regex = self.search_dialog.regex_check.isChecked()
            self._perform_search_async_prepare(
                lambda: self.search_manager.find_next(search_text, case_sensitive, use_regex),
                "検索中..."
            )
                
    def find_previous(self):
        """前を検索（Shift+F3）"""
        if not self.search_dialog or not self.search_dialog.isVisible():
            self.show_search_dialog()
            return
            
        search_text = self.search_dialog.search_input.text()
        if search_text:
            case_sensitive = self.search_dialog.case_sensitive_check.isChecked()
            use_regex = self.search_dialog.regex_check.isChecked()
            self._perform_search_async_prepare(
                lambda: self.search_manager.find_previous(search_text, case_sensitive, use_regex),
                "検索中..."
            )
    
    def _on_target_changed(self, target_key: str):
        """検索対象が変更された時"""
        self._set_search_target(target_key)
    
    def _set_search_target(self, target_key: str):
        """検索対象を設定"""
        if target_key in self.target_widgets:
            self.search_manager.set_text_widget(self.target_widgets[target_key])
        
    def _on_search_next(self, search_text: str, case_sensitive: bool, use_regex: bool):
        """検索ダイアログからの次を検索シグナル"""
        self._perform_search_async_prepare(
            lambda: self.search_manager.find_next(search_text, case_sensitive, use_regex),
            "検索中..."
        )
            
    def _on_search_previous(self, search_text: str, case_sensitive: bool, use_regex: bool):
        """検索ダイアログからの前を検索シグナル"""
        self._perform_search_async_prepare(
            lambda: self.search_manager.find_previous(search_text, case_sensitive, use_regex),
            "検索中..."
        )
            
    def _on_replace_one(self, search_text: str, replace_text: str,
                       case_sensitive: bool, use_regex: bool):
        """1つ置換"""
        success = self.search_manager.replace_current(search_text, replace_text,
                                                     case_sensitive, use_regex)
        if success:
            self._update_search_status()
        else:
            self._show_search_error("置換できませんでした")
                 
    def _on_replace_all(self, search_text: str, replace_text: str,
                       case_sensitive: bool, use_regex: bool):
        """すべて置換"""
        count = self.search_manager.replace_all(search_text, replace_text,
                                               case_sensitive, use_regex)
        if count > 0:
            self._show_search_status(f"{count} 個置換しました")
        else:
            self._show_search_status("置換する項目が見つかりませんでした")
                
    def _perform_search_async_prepare(self, search_func, status_message: str):
        """非同期検索の準備"""
        self._pending_search_params = search_func
        if self.search_dialog:
            self.search_dialog._show_status(status_message)
        self.search_timer.start(10)  # 10ms後に実行
        
    def _perform_search_async(self):
        """非同期で検索を実行"""
        if not self._pending_search_params:
            return
            
        try:
            search_func = self._pending_search_params
            self._pending_search_params = None
            
            # 実際の検索実行
            success = search_func()
            
            # 結果の表示
            if success:
                self._update_search_status()
            else:
                self._show_search_error("検索文字列が見つかりません")
                
        except Exception as e:
            self._show_search_error(f"検索エラー: {str(e)}")
            
    def _update_search_status(self):
        """検索ステータスを更新"""
        if not self.search_dialog:
            return
            
        current, total = self.search_manager.get_search_info()
        if total > 0:
            self.search_dialog._show_status(f"{current}/{total} 件")
        else:
            self.search_dialog._show_status("検索文字列が見つかりません", error=True)
            
    def _show_search_error(self, message: str):
        """検索エラーを表示"""
        if self.search_dialog:
            self.search_dialog._show_status(message, error=True)
        else:
            QMessageBox.warning(self.main_window, "検索エラー", message)
            
    def hide_search_dialog(self):
        """検索ダイアログを非表示"""
        if self.search_dialog:
            self.search_manager.clear_highlights()
            self.search_dialog.hide()
            
    def is_search_active(self) -> bool:
        """検索がアクティブかチェック"""
        return self.search_dialog is not None and self.search_dialog.isVisible()
