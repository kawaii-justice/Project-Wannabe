import re
from collections.abc import Mapping

from src.core.idea_processor import METADATA_MAP


def extract_metadata_value(
    selected_text: str,
    metadata_key: str,
    metadata_map: Mapping[str, str] = METADATA_MAP,
) -> str | None:
    """Extract one markdown metadata section from generated IDEA text."""
    target_name = metadata_map.get(metadata_key)
    if not target_name:
        raise KeyError(f"Unknown metadata key: {metadata_key}")

    extracted_lines: list[str] = []
    found_target = False
    for line in selected_text.splitlines():
        if not found_target:
            inline_value = _match_metadata_header(line, target_name)
            if inline_value is None:
                continue
            found_target = True
            if inline_value:
                extracted_lines.append(inline_value)
            continue

        if _is_next_metadata_header(line, metadata_key, metadata_map):
            break
        extracted_lines.append(line)

    if not found_target:
        return None
    return "\n".join(extracted_lines).strip()


def normalize_metadata_value(metadata_key: str, extracted_value: str) -> str | list[str]:
    if metadata_key == "title":
        return extracted_value.splitlines()[0] if extracted_value else ""
    if metadata_key in {"keywords", "genres"}:
        return parse_tag_lines(extracted_value)
    return extracted_value


def parse_tag_lines(text: str) -> list[str]:
    return [line.strip().lstrip("-").strip() for line in text.splitlines() if line.strip()]


def _is_next_metadata_header(line: str, metadata_key: str, metadata_map: Mapping[str, str]) -> bool:
    for key, japanese_name in metadata_map.items():
        if key != metadata_key and _match_metadata_header(line, japanese_name) is not None:
            return True
    return False


def _match_metadata_header(line: str, japanese_name: str) -> str | None:
    pattern = re.compile(rf"^\s*#{{1,6}}\s*{re.escape(japanese_name)}\s*(?:[:：]\s*(.*))?$")
    match = pattern.match(line)
    if not match:
        return None
    return (match.group(1) or "").strip()
