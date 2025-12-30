from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from PySide6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit

from src.core.dynamic_prompts import (
    BLOCK_COMMENT_PATTERN,
    BREAK_PATTERN,
    ENDPOINT_PATTERN,
    LINE_COMMENT_PATTERN,
)


Span = tuple[int, int]


def _normalize_spans(spans: Iterable[Span]) -> list[Span]:
    normalized: list[Span] = []
    for start, end in spans:
        if start is None or end is None:
            continue
        if end <= start:
            continue
        normalized.append((int(start), int(end)))
    normalized.sort()
    if not normalized:
        return normalized

    merged: list[Span] = [normalized[0]]
    for start, end in normalized[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _color_luminance(color) -> float:
    r = color.redF()
    g = color.greenF()
    b = color.blueF()
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _blend_colors(background, foreground, foreground_ratio: float):
    foreground_ratio = max(0.0, min(1.0, float(foreground_ratio)))
    background_ratio = 1.0 - foreground_ratio
    r = int(background.red() * background_ratio + foreground.red() * foreground_ratio)
    g = int(background.green() * background_ratio + foreground.green() * foreground_ratio)
    b = int(background.blue() * background_ratio + foreground.blue() * foreground_ratio)
    return type(background)(r, g, b)


@dataclass(frozen=True)
class _PaletteSignature:
    base_rgb: int
    text_rgb: int


class DynamicPromptSyntaxHighlighter(QSyntaxHighlighter):
    """
    Highlights Project Wannabe's prompt-control syntax:
    - Comment-out sections: @//..., @/*...@*/ (grey)
    - Control tags: @break, @startpoint, @endpoint (bold + accent)
    - Disabled ranges: before last break/startpoint, after first endpoint (grey)

    It optionally protects ranges (e.g., autocomplete ghost text) from being overridden.
    """

    def __init__(
        self,
        editor: QPlainTextEdit,
        *,
        protected_spans_provider: Optional[Callable[[], Iterable[Span]]] = None,
    ):
        super().__init__(editor.document())
        self._editor = editor
        editor.document().contentsChange.connect(self._on_contents_change)
        self._protected_spans_provider = protected_spans_provider

        self._format_disabled = QTextCharFormat()
        self._format_comment = QTextCharFormat()
        self._format_control = QTextCharFormat()

        self._cached_revision: Optional[int] = None
        self._cached_len: int = 0
        self._comment_spans: list[Span] = []
        self._disabled_spans: list[Span] = []
        self._control_spans: list[Span] = []
        self._protected_spans: list[Span] = []
        self._prev_comment_spans: list[Span] = []
        self._prev_control_spans: list[Span] = []
        self._palette_signature: Optional[_PaletteSignature] = None

        self.update_theme()

    def _touches_span(self, spans: Iterable[Span], start: int, end: int) -> bool:
        if end <= start:
            return False
        for span_start, span_end in spans:
            if span_end <= start:
                continue
            if span_start >= end:
                return False
            return True
        return False

    def _should_rehighlight_global(self, pos: int, chars_removed: int, chars_added: int) -> bool:
        if chars_removed <= 0 and chars_added <= 0:
            return False

        doc = self.document()
        if chars_added > 0:
            window_start = max(0, pos - 16)
            window_end = min(doc.characterCount() - 1, pos + chars_added + 16)
            window = doc.toPlainText()[window_start:window_end]
            if (
                "@break" in window
                or "@startpoint" in window
                or "@endpoint" in window
                or "@//" in window
                or "@/*" in window
                or "@*/" in window
            ):
                return True

        if chars_removed > 0 and self._cached_revision is not None:
            removed_start = pos
            removed_end = pos + chars_removed
            if self._touches_span(self._prev_control_spans, removed_start, removed_end):
                return True
            if self._touches_span(self._prev_comment_spans, removed_start, removed_end):
                return True

        return False

    def _on_contents_change(self, pos: int, chars_removed: int, chars_added: int):
        if self._should_rehighlight_global(pos, chars_removed, chars_added):
            self.rehighlight()

    def update_theme(self):
        palette = self._editor.palette()
        base = palette.base().color()
        text = palette.text().color()

        signature = _PaletteSignature(base_rgb=base.rgb(), text_rgb=text.rgb())
        if self._palette_signature == signature:
            return
        self._palette_signature = signature

        is_dark = _color_luminance(base) < 0.5

        disabled = _blend_colors(base, text, 0.35 if is_dark else 0.45)
        comment = _blend_colors(base, text, 0.40 if is_dark else 0.50)

        self._format_disabled = QTextCharFormat()
        self._format_disabled.setForeground(disabled)

        self._format_comment = QTextCharFormat()
        self._format_comment.setForeground(comment)

        self._format_control = QTextCharFormat()
        self._format_control.setFontWeight(QFont.Bold)
        control_color = palette.link().color()
        if is_dark and _color_luminance(control_color) < 0.55:
            control_color = _blend_colors(base, text, 0.75)
        elif (not is_dark) and _color_luminance(control_color) > 0.8:
            control_color = _blend_colors(base, control_color, 0.7)
        self._format_control.setForeground(control_color)

        self.rehighlight()

    def _ensure_cache(self):
        doc = self.document()
        revision = doc.revision()
        if self._cached_revision == revision:
            return

        self._prev_comment_spans = self._comment_spans
        self._prev_control_spans = self._control_spans
        text = doc.toPlainText()
        self._cached_revision = revision
        self._cached_len = len(text)

        comment_spans: list[Span] = []
        for match in BLOCK_COMMENT_PATTERN.finditer(text):
            comment_spans.append((match.start(), match.end()))
        for match in LINE_COMMENT_PATTERN.finditer(text):
            start = match.start()
            end = match.end()
            if end < len(text) and text[end] in "\r\n":
                if text[end] == "\r" and end + 1 < len(text) and text[end + 1] == "\n":
                    end += 2
                else:
                    end += 1
            comment_spans.append((start, end))
        comment_spans = _normalize_spans(comment_spans)
        self._comment_spans = comment_spans

        def in_comment(pos: int) -> bool:
            idx = bisect_right(comment_spans, (pos, 10**18)) - 1
            if idx < 0:
                return False
            start, end = comment_spans[idx]
            return start <= pos < end

        control_spans: list[Span] = []
        last_break_end: Optional[int] = None
        for match in BREAK_PATTERN.finditer(text):
            if in_comment(match.start()):
                continue
            last_break_end = match.end()
            control_spans.append((match.start(), match.end()))

        start_index = last_break_end if last_break_end is not None else 0

        endpoint_start: Optional[int] = None
        for match in ENDPOINT_PATTERN.finditer(text):
            if in_comment(match.start()):
                continue
            control_spans.append((match.start(), match.end()))
            if endpoint_start is None and match.start() >= start_index:
                endpoint_start = match.start()

        self._control_spans = _normalize_spans(control_spans)

        end_index = endpoint_start if endpoint_start is not None else len(text)

        disabled_spans: list[Span] = []
        if start_index > 0:
            disabled_spans.append((0, start_index))
        if end_index < len(text):
            disabled_spans.append((end_index, len(text)))
        self._disabled_spans = _normalize_spans(disabled_spans)

        protected_spans: list[Span] = []
        if self._protected_spans_provider is not None:
            try:
                protected_spans = _normalize_spans(self._protected_spans_provider())
            except Exception:
                protected_spans = []
        self._protected_spans = protected_spans

    def _iter_unprotected_segments(self, start: int, end: int) -> Iterable[Span]:
        if end <= start:
            return
        protected = self._protected_spans
        if not protected:
            yield (start, end)
            return

        idx = bisect_right(protected, (start, 10**18)) - 1
        if idx < 0:
            idx = 0

        cursor = start
        while idx < len(protected) and cursor < end:
            p_start, p_end = protected[idx]
            if p_end <= cursor:
                idx += 1
                continue
            if p_start >= end:
                break
            if cursor < p_start:
                yield (cursor, min(p_start, end))
            cursor = max(cursor, p_end)
            idx += 1

        if cursor < end:
            yield (cursor, end)

    def _apply_global_span(self, span: Span, fmt: QTextCharFormat, block_start: int, block_end: int):
        start, end = span
        if end <= block_start or start >= block_end:
            return
        start = max(start, block_start)
        end = min(end, block_end)
        for seg_start, seg_end in self._iter_unprotected_segments(start, end):
            self.setFormat(seg_start - block_start, seg_end - seg_start, fmt)

    def highlightBlock(self, text: str):
        self._ensure_cache()

        block = self.currentBlock()
        block_start = block.position()
        block_end = block_start + len(text)

        for span in self._disabled_spans:
            self._apply_global_span(span, self._format_disabled, block_start, block_end)

        for span in self._control_spans:
            self._apply_global_span(span, self._format_control, block_start, block_end)

        for span in self._comment_spans:
            self._apply_global_span(span, self._format_comment, block_start, block_end)
