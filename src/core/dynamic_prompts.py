import re
import random
from typing import List

# Regex to find {option1|option2|"option 3"} patterns
DYNAMIC_PROMPT_PATTERN = re.compile(r"\{([^}]+)\}")

# Regex to parse the options within a {..} block, handling quoted strings
# Handles double quotes, single quotes, or unquoted parts separated by |
OPTION_PARSE_PATTERN = re.compile(r'"[^"]*"|\'[^\']*\'|[^|]+')

BLOCK_COMMENT_PATTERN = re.compile(r"@/\*.*?@\*/", re.DOTALL)
LINE_COMMENT_PATTERN = re.compile(r"@//.*?$", re.MULTILINE)
BREAK_PATTERN = re.compile(r"@break|@startpoint")
ENDPOINT_PATTERN = re.compile(r"@endpoint")

def _parse_options(options_str: str) -> List[str]:
    """Parses the options string, respecting quotes."""
    options = []
    # Find all matches for quoted strings or unquoted parts
    matches = OPTION_PARSE_PATTERN.findall(options_str)
    for match in matches:
        match = match.strip() # Remove leading/trailing whitespace from the segment
        if match:
            # Keep quotes for now, they will be handled after selection
            options.append(match)
    # Filter out empty strings that might result from consecutive pipes or stripping
    return [opt for opt in options if opt]


def evaluate_dynamic_prompt(text: str) -> str:
    """
    Evaluates a string containing dynamic prompt syntax like {option1|option2|"option 3"},
    replacing each occurrence with a randomly chosen option.

    Args:
        text: The input string containing potential dynamic prompts. Can be None.

    Returns:
        The string with dynamic prompts evaluated, or the original text if input is None or invalid.
    """
    # Handle None input gracefully
    if text is None:
        return "" # Or return None, depending on desired behavior for None input
    if not isinstance(text, str):
        return text # Return early if not a string

    # Strip comment-out sections before dynamic prompt evaluation
    text = BLOCK_COMMENT_PATTERN.sub("", text)
    text = LINE_COMMENT_PATTERN.sub("", text)

    # Apply range control tags after comment stripping
    last_break = text.rfind("@break")
    last_startpoint = text.rfind("@startpoint")
    if last_break != -1 or last_startpoint != -1:
        if last_break >= last_startpoint:
            text = text[last_break + len("@break"):]
        else:
            text = text[last_startpoint + len("@startpoint"):]

    endpoint_pos = text.find("@endpoint")
    if endpoint_pos != -1:
        text = text[:endpoint_pos]

    if '{' not in text:
        return text # Return early if no dynamic prompts seem present

    # Use a function for re.sub to handle each match
    def replace_match(match):
        options_str = match.group(1) # Content inside {}
        options = _parse_options(options_str)
        if not options:
            # If parsing fails or yields no options, return the original match {content}
            return match.group(0)
        # Choose a random option
        chosen_option = random.choice(options)

        # Strip outer quotes (double or single) from the chosen option before returning
        if len(chosen_option) >= 2:
            if (chosen_option.startswith('"') and chosen_option.endswith('"')) or \
               (chosen_option.startswith("'") and chosen_option.endswith("'")):
                return chosen_option[1:-1] # Return content without quotes
        # If not quoted or too short to be quoted, return as is
        return chosen_option

    # Replace all occurrences
    evaluated_text = DYNAMIC_PROMPT_PATTERN.sub(replace_match, text)
    return evaluated_text


def is_position_valid(text: str, cursor_pos: int) -> bool:
    """
    Returns True if the cursor position is within the effective prompt range.
    """
    if not isinstance(text, str):
        return True
    if cursor_pos < 0 or cursor_pos > len(text):
        return False

    comment_spans = []
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

    def _in_comment(pos: int) -> bool:
        for start, end in comment_spans:
            if start <= pos < end:
                return True
        return False

    if _in_comment(cursor_pos):
        return False

    last_break_end = None
    for match in BREAK_PATTERN.finditer(text):
        if _in_comment(match.start()):
            continue
        last_break_end = match.end()

    start_index = last_break_end if last_break_end is not None else 0
    if cursor_pos < start_index:
        return False

    endpoint_start = None
    for match in ENDPOINT_PATTERN.finditer(text):
        if _in_comment(match.start()):
            continue
        if match.start() < start_index:
            continue
        endpoint_start = match.start()
        break

    if endpoint_start is not None and cursor_pos >= endpoint_start:
        return False

    return True

# --- Example Usage ---
if __name__ == "__main__":
    test_cases = [
        "This is a {simple|basic} test.",
        "Choose between {option A|option B|option C}.",
        "Select {\"quoted option 1\"|'quoted option 2'|unquoted option 3}.",
        "Mix: {A|\"B C\"|D|'E F G'}.",
        "No options: {}",
        "Empty options: {||}",
        "Single option: {lonely}",
        "Single quoted: {\"quoted lonely\"}",
        "Single single-quoted: {'single quoted lonely'}",
        "Nested (not supported): {A|{B|C}}", # Regex won't handle nesting correctly
        "Adjacent: {one|two}{three|four}",
        "Text with {dynamic|random} elements and {fixed|static} parts.",
        "Path: C:/Users/{UserA|UserB}/Documents",
        "Sentence with {a few|several|\"many different\"} choices.",
        "Leading/Trailing spaces: {  option1  |  \" option 2 \" | option3 }",
        "No dynamic prompts here.",
        "",
        None, # Test None input
        123, # Test non-string input
        "{A| B | C }", # Spaces around pipes
        "{\"A B\"|\"C D\"}", # Only quoted options
        "{'E F'|'G H'}", # Only single-quoted options
    ]

    print("--- Running Test Cases ---")
    for i, case in enumerate(test_cases):
        print(f"\n--- Case {i+1} ---")
        print(f"Input:  {repr(case)}") # Use repr to show None/int clearly
        try:
            output = evaluate_dynamic_prompt(case)
            print(f"Output: {repr(output)}")
        except Exception as e:
            print(f"Error: {e}")
        print("-" * 10)

    # Test randomness
    print("\n--- Randomness Test ---")
    test_random = "Result: {1|2|3|4|5}"
    results = set()
    print(f"Input: {test_random}")
    for _ in range(20):
        results.add(evaluate_dynamic_prompt(test_random))
    print(f"Outputs (20 runs, should contain multiple from 1-5):")
    for res in sorted(list(results)):
        print(f"- {res}")
