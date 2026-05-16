import re
from collections.abc import Mapping

from src.core.idea_processor import METADATA_MAP


def extract_metadata_value(
    selected_text: str,
    metadata_key: str,
    metadata_map: Mapping[str, str] = METADATA_MAP,
) -> str | None:
    """Extract one '# JapaneseName:' section from generated IDEA text."""
    target_name = metadata_map.get(metadata_key)
    if not target_name:
        raise KeyError(f"Unknown metadata key: {metadata_key}")

    pattern = re.compile(rf"# {re.escape(target_name)}:\s*(.*)", re.MULTILINE | re.DOTALL)
    match = pattern.search(selected_text)
    if not match:
        return None

    extracted_lines: list[str] = []
    for line in match.group(1).strip().splitlines():
        if _is_next_metadata_header(line, metadata_key, metadata_map):
            break
        extracted_lines.append(line)
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
    stripped = line.strip()
    for key, japanese_name in metadata_map.items():
        if key != metadata_key and stripped.startswith(f"# {japanese_name}:"):
            return True
    return False
