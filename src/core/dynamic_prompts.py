import re
import random
from typing import List

# Regex to find {option1|option2|"option 3"} patterns
DYNAMIC_PROMPT_PATTERN = re.compile(r"\{([^}]+)\}")

# Regex to parse the options within a {..} block, handling quoted strings
# Handles double quotes, single quotes, or unquoted parts separated by |
OPTION_PARSE_PATTERN = re.compile(r'"[^"]*"|\'[^\']*\'|[^|]+')

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
    if not isinstance(text, str) or '{' not in text:
        return text # Return early if not string or no dynamic prompts seem present

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
