import sys
import os # Add os import
from contextlib import contextmanager
from PySide6.QtWidgets import (QMenuBar, QFileDialog, QMessageBox, QDialog, QWidget, # Add QWidget
                               QFontDialog, QApplication, QCheckBox, QLabel, # Add QLabel
                               QVBoxLayout, QDialogButtonBox, QTextEdit, QPlainTextEdit, QLineEdit) # Add TextEdit types
from PySide6.QtGui import QAction, QKeySequence, QActionGroup, QFont # Add QFont
from PySide6.QtCore import Slot, Qt

# Import necessary components from the project
# Need to adjust imports based on actual MainWindow structure and project_io location
# Assuming MainWindow is passed and has necessary widgets accessible
# from main import MainWindow # Avoid circular import, pass MainWindow instance
from src.core.project_io import save_project_data, load_project_data, save_output_text, ProjectIOError
from src.core.settings import load_settings, save_settings # For theme/font settings

# Placeholder for theme switching logic (e.g., using qdarkstyle)
try:
    import qdarkstyle
    HAS_QDARKSTYLE = True
except ImportError:
    HAS_QDARKSTYLE = False
    # print("qdarkstyle not found. Theme switching will be basic.") # Removed print statement


class MenuHandler:
    """Handles the creation and actions of the main menu bar."""

    def __init__(self, main_window):
        # Store reference to the main window to access its widgets and methods
        self.main_window = main_window
        self.current_project_path: str | None = None # Store path for Save action

    @contextmanager
    def _suspend_autocomplete(self):
        """
        ファイル操作中にオートコンプリートを一時的に無効化し、
        ゴーストテキストの混入や競合を防ぐコンテキストマネージャ。
        """
        manager = getattr(self.main_window, 'autocomplete_manager', None)
        was_enabled = False
        
        if manager:
            was_enabled = manager.is_enabled
            # 無効化することで、ゴーストテキスト消去・タイマー停止・タスクキャンセルが一括で行われる
            manager.set_enabled(False)
            
        try:
            yield
        finally:
            # 元の状態が有効だった場合のみ復帰させる
            if manager and was_enabled:
                manager.set_enabled(True)
                # デバウンスタイマーを再始動して、次の入力を監視
                manager.debounce_timer.start()

    def create_menu_bar(self) -> QMenuBar:
        """Creates and returns the main QMenuBar."""
        menu_bar = self.main_window.menuBar() # Get menu bar from main window
        menu_bar.clear() # Clear any existing menus

        self._create_file_menu(menu_bar)
        self._create_edit_menu(menu_bar)
        self._create_view_menu(menu_bar)
        self._create_generate_menu(menu_bar) # Keep generate menu from main.py for now
        self._create_settings_menu(menu_bar) # Keep settings menu from main.py for now
        self._create_help_menu(menu_bar)

        return menu_bar

    # --- File Menu ---
    def _create_file_menu(self, menu_bar: QMenuBar):
        file_menu = menu_bar.addMenu("ファイル(&F)")

        open_action = QAction("開く...", self.main_window)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)

        save_as_action = QAction("名前を付けて保存...", self.main_window)
        save_as_action.setShortcut(QKeySequence.SaveAs)
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)

        # Add a 'Save' action (optional, saves to current_project_path)
        save_action = QAction("保存", self.main_window)
        save_action.setShortcut(QKeySequence.Save) # Ctrl+S
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        export_output_action = QAction("出力内容を書き出し...", self.main_window)
        export_output_action.triggered.connect(self._export_output)
        file_menu.addAction(export_output_action)

        file_menu.addSeparator()

        exit_action = QAction("終了", self.main_window)
        exit_action.setShortcut(QKeySequence.Quit)
        exit_action.triggered.connect(self.main_window.close) # Connect to main window's close
        file_menu.addAction(exit_action)

    @Slot()
    def _open_project(self):
        """Handles the 'File > Open...' action."""
        with self._suspend_autocomplete():
            filepath, _ = QFileDialog.getOpenFileName(
                self.main_window,
                "プロジェクトファイルを開く",
                "", # Start directory (optional)
                "Project Wannabe Files (*.json);;All Files (*)"
            )
            if not filepath:
                return

            try:
                project_data = load_project_data(filepath)
                self._apply_project_data(project_data)
                self.current_project_path = filepath
                self.main_window.status_bar.showMessage(f"プロジェクト '{os.path.basename(filepath)}' を読み込みました。", 5000)
                self.main_window.setWindowTitle(f"Project Wannabe - {os.path.basename(filepath)}")
            except ProjectIOError as e:
                QMessageBox.critical(self.main_window, "読み込みエラー", f"プロジェクトファイルの読み込みに失敗しました:\n{e}")
                self.current_project_path = None
                self.main_window.setWindowTitle("Project Wannabe (仮称)")


    @Slot()
    def _save_project_as(self):
        """Handles the 'File > Save As...' action."""
        with self._suspend_autocomplete():
            filepath, _ = QFileDialog.getSaveFileName(
                self.main_window,
                "プロジェクトを名前を付けて保存",
                self.current_project_path or "", # Start directory
                "Project Wannabe Files (*.json);;All Files (*)"
            )
            if not filepath:
                return

            # Ensure the extension is .json
            if not filepath.lower().endswith(".json"):
                filepath += ".json"

            try:
                project_data = self._collect_project_data()
                save_project_data(filepath, project_data)
                self.current_project_path = filepath
                self.main_window.status_bar.showMessage(f"プロジェクトを '{os.path.basename(filepath)}' として保存しました。", 5000)
                self.main_window.setWindowTitle(f"Project Wannabe - {os.path.basename(filepath)}")
            except (ProjectIOError, ValueError) as e: # Catch potential errors from collect_project_data
                QMessageBox.critical(self.main_window, "保存エラー", f"プロジェクトの保存に失敗しました:\n{e}")


    @Slot()
    def _save_project(self):
        """Handles the 'File > Save' action."""
        with self._suspend_autocomplete():
            if not self.current_project_path:
                # If no path is set (e.g., new project or opened without saving yet),
                # delegate to Save As...
                self._save_project_as()
                return

            # If a path exists, proceed with saving to that path
            try:
                project_data = self._collect_project_data()
                save_project_data(self.current_project_path, project_data)
                # Use os.path.basename to show only the filename in the status message
                self.main_window.status_bar.showMessage(f"プロジェクト '{os.path.basename(self.current_project_path)}' を上書き保存しました。", 3000)
            except (ProjectIOError, ValueError) as e:
                QMessageBox.critical(self.main_window, "上書き保存エラー", f"プロジェクトの上書き保存に失敗しました:\n{e}")


    @Slot()
    def _export_output(self):
        """Handles the 'File > Export Output...' action."""
        with self._suspend_autocomplete():
            # Create a custom dialog or use QMessageBox with a checkbox
            dialog = QDialog(self.main_window)
            dialog.setWindowTitle("出力内容を書き出し")
            layout = QVBoxLayout(dialog)
            label = QLabel("出力内容をテキストファイルとして書き出します。")
            checkbox = QCheckBox("ファイル冒頭に現在のタイトルを含める")
            checkbox.setChecked(True) # Default to include title
            button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)

            layout.addWidget(label)
            layout.addWidget(checkbox)
            layout.addWidget(button_box)

            button_box.accepted.connect(dialog.accept)
            button_box.rejected.connect(dialog.reject)

            if dialog.exec() != QDialog.Accepted:
                return

            include_title = checkbox.isChecked()
            current_title = self.main_window.title_edit.text() if hasattr(self.main_window, 'title_edit') else None

            if include_title and not current_title:
                # 警告を出してユーザーに確認
                reply = QMessageBox.warning(
                    self.main_window,
                    "タイトル未設定",
                    "タイトルを含めるオプションが選択されましたが、詳細情報タブでタイトルが設定されていません。\n\n無効にして保存を続行しますか？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    # ユーザーがOKしたらタイトル含むオプションを無効にして続行
                    include_title = False
                else:
                    # キャンセルしたら処理を中断
                    return

            filepath, _ = QFileDialog.getSaveFileName(
                self.main_window,
                "本文内容を書き出し", # Dialog title changed
                "", # Start directory
                "Text Files (*.txt);;All Files (*)"
            )
            if not filepath:
                return

            # Ensure the extension is .txt
            if not filepath.lower().endswith(".txt"):
                filepath += ".txt"

            try:
                # *** FIX: Get text from main_text_edit, not output_text_edit ***
                if not hasattr(self.main_window, 'main_text_edit'):
                     raise AttributeError("本文エリア (main_text_edit) が見つかりません。")
                output_text = self.main_window.main_text_edit.toPlainText() # Changed target widget

                # --- DEBUGGING START ---
                print(f"--- Debug Export Output ---")
                print(f"Accessing widget: self.main_window.main_text_edit") # Changed widget name
                print(f"Widget objectName: {self.main_window.main_text_edit.objectName() if hasattr(self.main_window.main_text_edit, 'objectName') else 'N/A'}") # Check object name if set
                print(f"Widget Type: {type(self.main_window.main_text_edit)}") # Changed widget type
                print(f"Include Title: {include_title}, Title: {current_title}")
                print(f"Output Text Length: {len(output_text)}")
                # print(f"Output Text Content:\n---\n{output_text[:500]}...\n---") # Print first 500 chars
                # --- DEBUGGING END ---

                save_output_text(filepath, output_text, include_title, current_title)
                self.main_window.status_bar.showMessage(f"出力内容を '{os.path.basename(filepath)}' に書き出しました。", 5000)
            except (ProjectIOError, ValueError, AttributeError) as e: # Added AttributeError
                QMessageBox.critical(self.main_window, "書き出しエラー", f"出力内容の書き出しに失敗しました:\n{e}")
            # except AttributeError: # Handled above
            #      QMessageBox.critical(self.main_window, "エラー", "出力テキストエリアが見つかりません。")


    def _collect_project_data(self) -> dict:
        """Collects data from UI elements to be saved."""
        # *** FIX: More robust check for attribute existence ***
        required_ui = {
            'title': 'title_edit', 'keywords': 'keywords_widget', 'genres': 'genre_widget',
            'synopsis': 'synopsis_edit', 'setting': 'setting_edit', 'plot': 'plot_edit',
            'dialogue_level': 'dialogue_level_combo',
            'rating': 'rating_combo_details', # Add rating combo from details tab
            'authors_note': 'authors_note_edit', # Add authors_note edit
            'main_text': 'main_text_edit', 'memo': 'memo_edit'
        }
        missing_attrs = [name for name, attr in required_ui.items() if not hasattr(self.main_window, attr)]
        if missing_attrs:
            raise ValueError(f"メインウィンドウに必要なUI要素が見つかりません: {', '.join(missing_attrs)}")

        details = {
            "title": self.main_window.title_edit.text(),
            "keywords": self.main_window.keywords_widget.get_tags(),
            "genres": self.main_window.genre_widget.get_tags(),
            "synopsis": self.main_window.synopsis_edit.toPlainText(),
            "setting": self.main_window.setting_edit.toPlainText(),
            "plot": self.main_window.plot_edit.toPlainText(),
            "dialogue_level": self.main_window.dialogue_level_combo.currentText(),
            "rating": self.main_window.rating_combo_details.currentData(), # Save selected rating data
            "authors_note": self.main_window.authors_note_edit.toPlainText(), # Add authors_note
        }
        main_text = self.main_window.main_text_edit.toPlainText()
        memo_text = self.main_window.memo_edit.toPlainText()

        return {
            "details": details,
            "main_text": main_text,
            "memo_text": memo_text
        }

    def _apply_project_data(self, data: dict):
        """Applies loaded data to UI elements."""
        # *** FIX: More robust check and application ***
        required_ui = {
            'title': 'title_edit', 'keywords': 'keywords_widget', 'genres': 'genre_widget',
            'synopsis': 'synopsis_edit', 'setting': 'setting_edit', 'plot': 'plot_edit',
            'authors_note': 'authors_note_edit', # Add authors_note edit check
            'dialogue_level': 'dialogue_level_combo',
            'rating': 'rating_combo_details', # Add rating combo from details tab
            'main_text': 'main_text_edit', 'memo': 'memo_edit',
            'output_clear': 'output_text_edit', 'output_counter': 'output_block_counter'
        }
        missing_attrs = [name for name, attr in required_ui.items() if not hasattr(self.main_window, attr)]
        if missing_attrs:
            raise ValueError(f"メインウィンドウに必要なUI要素が見つかりません: {', '.join(missing_attrs)}")

        details = data.get("details", {})
        # Apply details safely
        self.main_window.title_edit.setText(details.get("title", "") or "") # Ensure string
        self.main_window.keywords_widget.set_tags(details.get("keywords", []) or []) # Ensure list
        self.main_window.genre_widget.set_tags(details.get("genres", []) or []) # Ensure list
        self.main_window.synopsis_edit.setPlainText(details.get("synopsis", "") or "")
        self.main_window.setting_edit.setPlainText(details.get("setting", "") or "")
        self.main_window.plot_edit.setPlainText(details.get("plot", "") or "")
        self.main_window.authors_note_edit.setPlainText(details.get("authors_note", "") or "") # Add authors_note
        # Apply dialogue level safely
        self.main_window.dialogue_level_combo.setCurrentText(details.get("dialogue_level", "指定なし") or "指定なし")
        # Apply rating safely
        loaded_rating = details.get("rating", "general") or "general" # Default to general if missing/empty
        rating_index = self.main_window.rating_combo_details.findData(loaded_rating)
        if rating_index != -1:
            self.main_window.rating_combo_details.setCurrentIndex(rating_index)
        else: # Fallback if loaded rating value is invalid
            self.main_window.rating_combo_details.setCurrentIndex(self.main_window.rating_combo_details.findData("general"))


        # Apply main text and memo safely
        self.main_window.main_text_edit.setPlainText(data.get("main_text", "") or "") # Ensure string
        self.main_window.memo_edit.setPlainText(data.get("memo_text", "") or "") # Ensure string

        # Reset output area and counter when loading a project
        self.main_window.output_text_edit.clear()
        self.main_window.output_block_counter = 1


    # --- Edit Menu ---
    def _create_edit_menu(self, menu_bar: QMenuBar):
        edit_menu = menu_bar.addMenu("編集(&E)")

        # 検索機能の初期化
        if not hasattr(self, 'search_handler'):
            from src.ui.search_handler import SearchHandler
            self.search_handler = SearchHandler(self.main_window)

        # 検索アクションを追加
        search_actions = self.search_handler.create_search_actions()
        for action in search_actions:
            edit_menu.addAction(action)
            
        edit_menu.addSeparator()

        undo_action = QAction("元に戻す", self.main_window)
        undo_action.setShortcut(QKeySequence.Undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("やり直し", self.main_window)
        redo_action.setShortcut(QKeySequence.Redo)
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        cut_action = QAction("切り取り", self.main_window)
        cut_action.setShortcut(QKeySequence.Cut)
        edit_menu.addAction(cut_action)

        copy_action = QAction("コピー", self.main_window)
        copy_action.setShortcut(QKeySequence.Copy)
        edit_menu.addAction(copy_action)

        paste_action = QAction("貼り付け", self.main_window)
        paste_action.setShortcut(QKeySequence.Paste)
        edit_menu.addAction(paste_action)

        edit_menu.addSeparator()

        select_all_action = QAction("すべて選択", self.main_window)
        select_all_action.setShortcut(QKeySequence.SelectAll)
        edit_menu.addAction(select_all_action)

        # Connect actions to focused widget's slots
        undo_action.triggered.connect(self._handle_edit_action(lambda w: w.undo()))
        redo_action.triggered.connect(self._handle_edit_action(lambda w: w.redo()))
        cut_action.triggered.connect(self._handle_edit_action(lambda w: w.cut()))
        copy_action.triggered.connect(self._handle_edit_action(lambda w: w.copy()))
        paste_action.triggered.connect(self._handle_edit_action(lambda w: w.paste()))
        select_all_action.triggered.connect(self._handle_edit_action(lambda w: w.selectAll()))

    def _handle_edit_action(self, action_func):
        """Helper to call an action on the focused widget if applicable."""
        def handler():
            widget = QApplication.focusWidget()
            # *** FIX: Check if widget is an instance of known editable types ***
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                # Check if the specific action method exists before calling
                method_name = action_func.__name__
                # For standard methods like 'cut', 'copy', 'paste', 'selectAll', 'undo', 'redo'
                # Python's lambda captures the function object, its __name__ is '<lambda>'
                # We need a way to map lambda to the actual method name or check capabilities.
                # Let's try calling directly and catch AttributeError.
                try:
                    action_func(widget)
                except AttributeError:
                     print(f"Focused widget {type(widget).__name__} does not support this specific action.")
                except Exception as e:
                    print(f"Error executing edit action on {widget}: {e}")
            else:
                # Optionally provide feedback if focus is not on an editable widget
                # print(f"Focused widget {type(widget).__name__} is not an editable text widget.")
                pass # Or show status bar message
        return handler


    # --- View Menu ---
    def _create_view_menu(self, menu_bar: QMenuBar):
        view_menu = menu_bar.addMenu("表示(&V)")

        # Theme switching
        theme_menu = view_menu.addMenu("テーマ切り替え")
        # Always create a new group owned by the handler or main window
        self.theme_group = QActionGroup(self.main_window)
        self.theme_group.setExclusive(True)

        light_theme_action = QAction("ライト", self.main_window, checkable=True)
        dark_theme_action = QAction("ダーク", self.main_window, checkable=True)

        self.theme_group.addAction(light_theme_action)
        self.theme_group.addAction(dark_theme_action)
        theme_menu.addAction(light_theme_action)
        theme_menu.addAction(dark_theme_action)

        light_theme_action.triggered.connect(lambda: self._set_theme("light"))
        dark_theme_action.triggered.connect(lambda: self._set_theme("dark"))

        # Load initial theme state from settings
        settings = load_settings()
        current_theme = settings.get("theme", "light") # Default to light
        if current_theme == "dark":
            dark_theme_action.setChecked(True)
            self._apply_theme("dark") # Apply initial theme
        else:
            light_theme_action.setChecked(True)
            self._apply_theme("light") # Apply initial theme


        # Font settings
        font_action = QAction("フォント設定...", self.main_window)
        font_action.triggered.connect(self._open_font_dialog)
        view_menu.addAction(font_action)

        # Apply initial font from settings
        self._apply_initial_font()


    @Slot()
    def _set_theme(self, theme_name: str):
        """Applies the selected theme and saves it to settings."""
        if self._apply_theme(theme_name):
            settings = load_settings()
            settings["theme"] = theme_name
            save_settings(settings)
            self.main_window.status_bar.showMessage(f"テーマを「{theme_name}」に切り替えました。", 3000)
            # Force redraw/repolish of widgets if necessary
            self.main_window.style().unpolish(QApplication.instance())
            self.main_window.style().polish(QApplication.instance())
            self.main_window.update()
            if hasattr(self.main_window, "_on_theme_changed"):
                self.main_window._on_theme_changed(theme_name)


    def _apply_theme(self, theme_name: str) -> bool:
        """Applies the visual theme."""
        current_stylesheet = ""
        success = False
        if theme_name == "dark" and HAS_QDARKSTYLE:
            try:
                current_stylesheet = qdarkstyle.load_stylesheet(qt_api='pyside6')
                success = True
            except Exception as e:
                print(f"Error loading qdarkstyle: {e}")
                QMessageBox.warning(self.main_window, "テーマエラー", "ダークテーマ (qdarkstyle) の読み込みに失敗しました。")
                # Fallback to default or clear stylesheet
                current_stylesheet = ""
                success = False # Indicate failure
        elif theme_name == "light":
            # Reset to default Qt style by setting an empty stylesheet
            current_stylesheet = ""
            success = True
        else: # Basic dark theme fallback if qdarkstyle is not available
             if theme_name == "dark":
                 # Very basic dark theme - consider expanding or using a dedicated library
                 current_stylesheet = """
                     QWidget { background-color: #333; color: #EEE; }
                     QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
                         background-color: #444; color: #EEE; border: 1px solid #555;
                     }
                     QPushButton, QToolButton { background-color: #555; color: #EEE; border: 1px solid #666; padding: 3px; }
                     QPushButton:hover, QToolButton:hover { background-color: #666; }
                     QMenuBar, QMenu { background-color: #444; color: #EEE; }
                     QMenuBar::item:selected, QMenu::item:selected { background-color: #555; }
                     QStatusBar { background-color: #444; color: #EEE; }
                     QTabWidget::pane { border: 1px solid #555; }
                     QTabBar::tab { background-color: #444; color: #EEE; padding: 5px; }
                     QTabBar::tab:selected { background-color: #555; }
                     QScrollArea { border: none; background-color: #333; }
                     /* Specific fix for TagWidget */
                     TagWidget QLabel { color: #EEE; background: transparent; } /* Tag text, ensure transparent background */
                     TagWidget QPushButton { color: #EEE; background-color: #666; border: none; font-weight: bold; border-radius: 8px; } /* Remove button */
                     TagWidget QPushButton:hover { background-color: #777; }
                     /* Target the specific QWidget holding the tag label and button */
                     TagWidget > QWidget[objectName="tags_display_widget"] > QWidget { background-color: #555; border-radius: 5px; padding: 1px 3px; } /* Tag background */
                 """
                 success = True
             else: # Should be light, handled above
                 success = False # Should not happen if theme_name is 'light'

        # Apply the main stylesheet first
        self.main_window.setStyleSheet(current_stylesheet)

        # Explicitly update TagWidget styles after main stylesheet is applied
        # This ensures these specific styles override the general ones if needed.
        self._update_tag_widget_style(theme_name)

        return success

    def _update_tag_widget_style(self, theme_name: str):
        """Updates TagWidget styles based on the theme. Call this *after* setting the main stylesheet."""
        try:
            # Find all TagWidgets within the main window
            # Use findChildren with the actual type if possible, otherwise by name
            from src.ui.widgets import TagWidget # Import locally to avoid circularity at top level
            tag_widgets = self.main_window.findChildren(TagWidget)

            if not tag_widgets:
                # print("No TagWidgets found to update style.")
                return

            if theme_name == "dark":
                label_style = "color: #EEE; background: transparent;"
                button_style = "QPushButton { border: none; font-weight: bold; color: #EEE; background-color: #666; border-radius: 8px; } QPushButton:hover { background-color: #777; }"
                tag_bg_style = "QWidget { background-color: #555; border-radius: 5px; padding: 1px 3px; }"
            else: # light
                label_style = "color: black; background: transparent;"
                button_style = "QPushButton { border: none; font-weight: bold; color: black; background-color: #cccccc; border-radius: 8px; } QPushButton:hover { background-color: #bbbbbb; }"
                tag_bg_style = "QWidget { background-color: #d3d3d3; border-radius: 5px; padding: 1px 3px; }"

            for tag_widget in tag_widgets:
                # Update the container for the flow layout if needed (usually inherits)
                # tag_widget.tags_display_widget.setStyleSheet(...)

                # Update existing tag items (label and button)
                flow_layout = tag_widget.tags_layout
                for i in range(flow_layout.count()):
                    item = flow_layout.itemAt(i)
                    if item and item.widget():
                        tag_item_widget = item.widget() # This is the QWidget containing label+button
                        tag_item_widget.setStyleSheet(tag_bg_style) # Style the container
                        label = tag_item_widget.findChild(QLabel)
                        button = tag_item_widget.findChild(QPushButton)
                        if label:
                            label.setStyleSheet(label_style) # Style the label itself
                        if button:
                            button.setStyleSheet(button_style) # Style the button itself
        except Exception as e:
            print(f"Error updating TagWidget style: {e}")



    @Slot()
    def _open_font_dialog(self):
        """Opens the font selection dialog and applies the chosen font."""
        settings = load_settings()
        current_font_str = settings.get("font_family")
        current_font_size = settings.get("font_size")
        initial_font = QApplication.font() # Default fallback
        if current_font_str:
            initial_font.setFamily(current_font_str)
        if isinstance(current_font_size, int):
             initial_font.setPointSize(current_font_size)

        # *** FIX: Swap variable assignment to match actual return order (QFont, bool) ***
        returned_font_obj, returned_ok_bool = QFontDialog.getFont(initial_font, self.main_window, "フォントを選択") # Original incorrect assignment kept for context, but logic below uses correct order based on debug output

        # Re-assign based on the *actual* observed return order from debug logs: QFont first, then bool
        actual_font_obj, actual_ok_bool = returned_font_obj, returned_ok_bool # Temporarily store incorrect assignment
        returned_font_obj, returned_ok_bool = actual_ok_bool, actual_font_obj # Correct the assignment based on debug output

        # Use the correctly assigned variables based on the observed types
        if returned_ok_bool and isinstance(returned_font_obj, QFont):
             self._apply_font(returned_font_obj) # Pass the correctly assigned QFont object

             # *** FIX: Ensure both family and size are saved correctly ***
             try:
                 settings = load_settings() # Load current settings
                 settings["font_family"] = returned_font_obj.family() # Update family
                 settings["font_size"] = returned_font_obj.pointSize() # Update size
                 save_settings(settings) # Save the complete updated settings
                 self.main_window.status_bar.showMessage(f"フォントを「{returned_font_obj.family()} {returned_font_obj.pointSize()}pt」に設定しました。", 3000) # Use corrected variable
             except Exception as e:
                 print(f"Error saving font settings: {e}")
                 QMessageBox.warning(self.main_window, "設定保存エラー", f"フォント設定の保存中にエラーが発生しました:\n{e}")

        elif not returned_ok_bool: # Handle case where user cancelled (using corrected bool)
             print("Font selection cancelled.")
        else: # ok is True but font is not QFont (using corrected variables)
             print(f"Error: Font dialog returned ok={returned_ok_bool}, but font object is not QFont: {type(returned_font_obj)}")


    def _apply_font(self, font: QFont): # Add type hint
        """Applies the font to relevant widgets."""
        # *** FIX: Correctly identify widgets and apply font safely ***
        if not isinstance(font, QFont):
            print(f"Error in _apply_font: Expected QFont, got {type(font)}")
            return

        widgets_to_update = []
        # Add main text areas if they exist
        for attr_name in ['main_text_edit', 'output_text_edit', 'memo_edit',
                          'synopsis_edit', 'setting_edit', 'plot_edit', 'title_edit']:
            widget = getattr(self.main_window, attr_name, None)
            if widget and isinstance(widget, (QLineEdit, QPlainTextEdit)):
                widgets_to_update.append(widget)

        # Add TagWidget input and labels
        try:
            from src.ui.widgets import TagWidget # Import locally
            tag_widgets = self.main_window.findChildren(TagWidget)
            for tw in tag_widgets:
                if hasattr(tw, 'tag_input') and isinstance(tw.tag_input, QLineEdit):
                    widgets_to_update.append(tw.tag_input)
                if hasattr(tw, 'tags_layout'):
                    flow_layout = tw.tags_layout
                    for i in range(flow_layout.count()):
                        item = flow_layout.itemAt(i)
                        if item and item.widget():
                            label = item.widget().findChild(QLabel)
                            if label and isinstance(label, QLabel):
                                widgets_to_update.append(label)
        except Exception as e:
            print(f"Error finding TagWidget elements for font update: {e}")


        # Apply the font
        for i, widget in enumerate(widgets_to_update):
             try:
                 # Double check widget is valid and font is QFont before setting
                 if widget and isinstance(widget, QWidget) and isinstance(font, QFont):
                     widget.setFont(font)
                 else:
                      # Optional: Log skipping if needed for future debugging, but remove print for production
                      # print(f"Skipping font set for widget {i}: {widget} (Type: {type(widget)}) with font type: {type(font)}")
                      pass
             except Exception as e:
                 print(f"Error setting font for widget {i} ({widget}): {e}") # Keep error print for potential issues


    def _apply_initial_font(self):
        """Applies the font loaded from settings on startup."""
        settings = load_settings()
        font_family = settings.get("font_family")
        font_size = settings.get("font_size")

        if font_family or font_size:
            font = QApplication.font() # Start with default
            if font_family:
                font.setFamily(font_family)
            if isinstance(font_size, int) and font_size > 0:
                font.setPointSize(font_size)
            self._apply_font(font)


    # --- Generate Menu (Keep from main.py for now) ---
    def _create_generate_menu(self, menu_bar: QMenuBar):
         # This should ideally be handled by main_window itself or another controller
         # Replicating structure from main.py for now
        generate_menu = menu_bar.addMenu("生成(&G)")
        if hasattr(self.main_window, '_trigger_single_generation') and hasattr(self.main_window, '_toggle_infinite_generation'):
            single_gen_action = QAction("単発生成 (Ctrl+G)", self.main_window)
            single_gen_action.setShortcut("Ctrl+G")
            single_gen_action.triggered.connect(self.main_window._trigger_single_generation)
            generate_menu.addAction(single_gen_action)
            # Store reference if needed elsewhere
            self.main_window.single_gen_action = single_gen_action

            infinite_gen_action = QAction("無限生成 開始/停止 (F5)", self.main_window)
            infinite_gen_action.setShortcut("F5")
            infinite_gen_action.setCheckable(True)
            infinite_gen_action.triggered.connect(self.main_window._toggle_infinite_generation)
            generate_menu.addAction(infinite_gen_action)
            # Store reference if needed elsewhere
            self.main_window.infinite_gen_action = infinite_gen_action
        else:
             placeholder = generate_menu.addAction("(生成機能未接続)")
             placeholder.setEnabled(False)


    # --- Settings Menu (Keep from main.py for now) ---
    def _create_settings_menu(self, menu_bar: QMenuBar):
        # This should ideally be handled by main_window itself or another controller
        settings_menu = menu_bar.addMenu("設定(&S)")
        if hasattr(self.main_window, '_open_kobold_config_dialog') and hasattr(self.main_window, '_open_gen_params_dialog'):
            kobold_config_action = settings_menu.addAction("KoboldCpp 設定...")
            gen_params_action = settings_menu.addAction("生成パラメータ設定...")
            autocomplete_settings_action = settings_menu.addAction("設定: リアルタイム提案（ベータ）...")
            kobold_config_action.triggered.connect(self.main_window._open_kobold_config_dialog)
            gen_params_action.triggered.connect(self.main_window._open_gen_params_dialog)
            autocomplete_settings_action.triggered.connect(self._open_autocomplete_settings_dialog)
        else:
             placeholder = settings_menu.addAction("(設定機能未接続)")
             placeholder.setEnabled(False)


    # --- Help Menu ---
    def _create_help_menu(self, menu_bar: QMenuBar):
        help_menu = menu_bar.addMenu("ヘルプ(&H)")
        about_action = QAction("バージョン情報", self.main_window)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    @Slot()
    def _open_autocomplete_settings_dialog(self):
        """Opens the autocomplete settings dialog."""
        from src.ui.autocomplete_settings_dialog import AutocompleteSettingsDialog
        
        if AutocompleteSettingsDialog.show_dialog(self.main_window):
            # 設定が変更された場合、AutocompleteManagerに再読み込みを通知
            if hasattr(self.main_window, 'autocomplete_manager'):
                self.main_window.autocomplete_manager.reload_settings()
                self.main_window.status_bar.showMessage("リアルタイム提案（ベータ）の設定を更新しました。", 3000)
            else:
                print("Warning: autocomplete_manager not found in main_window")

    @Slot()
    def _show_about_dialog(self):
        """Shows the About dialog."""
        # Define version and app name here or get from a central place
        app_name = "Project Wannabe"
        version = "0.1.0" # Example version
        about_text = f"""
        <h2>{app_name}</h2>
        <p>バージョン: {version}</p>
        <p>カスタムLLMを活用した小説執筆支援ツール</p>
        <p>(C) 2025 kawaii-justice</p>
        """
        QMessageBox.about(self.main_window, f"{app_name} について", about_text)
