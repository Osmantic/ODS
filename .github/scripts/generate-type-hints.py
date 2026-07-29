#!/usr/bin/env python3
"""
Type Hints Generator using Claude Haiku 4.5

Generates type hints for functions missing annotations using Claude Haiku 4.5.
Processes in batches of 10 functions per API call for cost efficiency.

Cost: $27-47 per run (depending on function count and complexity)
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

from anthropic_helper import create_message


def load_missing_hints(hints_path: Path) -> list[dict[str, Any]]:
    """Load functions without type hints from JSON."""
    try:
        with open(hints_path, "r") as f:
            hints = json.load(f)
        return hints
    except FileNotFoundError:
        print(f"::error::Hints file not found: {hints_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"::error::Invalid JSON in hints file: {hints_path}", file=sys.stderr)
        sys.exit(1)


def read_function_code(file_path: str, function_name: str, line: int) -> str:
    """Read the function code from file."""
    try:
        with open(file_path, "r") as f:
            lines = f.readlines()

        start_line = line - 1
        code_lines = []
        indent_level = None

        for i in range(start_line, len(lines)):
            line_text = lines[i]

            if indent_level is None:
                indent_level = len(line_text) - len(line_text.lstrip())

            current_indent = len(line_text) - len(line_text.lstrip())
            if i > start_line and current_indent <= indent_level and line_text.strip():
                if line_text.strip().startswith(("def ", "class ", "@")):
                    break

            code_lines.append(line_text)

            if len(code_lines) >= 50:
                break

        return "".join(code_lines)
    except Exception as e:
        return f"# Could not read function code: {e}"


def build_type_hints_prompt(functions: list[dict[str, Any]]) -> str:
    """Build the prompt for generating type hints."""
    functions_formatted = []

    for i, func in enumerate(functions, 1):
        code = read_function_code(func["file"], func["function"], func["line"])
        functions_formatted.append(f"""
### Function {i}: `{func["function"]}` in `{func["file"]}`

```python
{code}
```
""")

    functions_text = "\n".join(functions_formatted)

    return f"""You are a Python type hints expert. Generate accurate type hints for functions missing annotations.

## Functions to Annotate ({len(functions)} total)

{functions_text}

## Your Task

For each function, provide:
1. **Typed function signature** with parameter and return type annotations
2. **Import statements** needed for the types
3. **Justification** explaining your type choices

## Guidelines

- Use standard library types when possible (`list`, `dict`, `str`, `int`, etc.)
- Use `typing` module for complex types (`List[str]`, `Optional[int]`, `Union`, etc.)
- Use `Any` sparingly - only when truly dynamic
- For async functions, annotate return type as `Awaitable[T]` or use `async def`
- Consider None returns: use `Optional[T]` if function can return None
- Look at parameter usage in function body to infer types
- Check existing return statements for return type hints

## Output Format

Respond with JSON:

```json
{{
  "functions_annotated": [
    {{
      "file": "app/example.py",
      "function": "process_data",
      "line": 42,
      "original_signature": "def process_data(data, options=None):",
      "typed_signature": "def process_data(data: List[dict], options: Optional[dict] = None) -> dict:",
      "imports_needed": ["from typing import List, Optional"],
      "justification": "data is iterated as list of dicts, options checked for None, returns dict"
    }}
  ],
  "summary": {{
    "total_annotated": 5,
    "imports_added": ["typing.List", "typing.Optional", "typing.Union"]
  }}
}}
```

Begin your analysis."""


def generate_type_hints_batch(
    functions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate type hints for a batch of functions using Claude Haiku 4.5."""
    prompt = build_type_hints_prompt(functions)

    try:
        response = create_message(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = ""
        for block in response.content:
            if block.type == "text":
                response_text += block.text

        json_start = response_text.find("```json")
        if json_start != -1:
            json_start = response_text.find("\n", json_start) + 1
            json_end = response_text.find("```", json_start)
            response_text = response_text[json_start:json_end].strip()
        else:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                response_text = response_text[json_start:json_end]

        result = json.loads(response_text)

        result["_metadata"] = {
            "model": "claude-haiku-4-5-20251001",
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }

        return result

    except json.JSONDecodeError:
        print("::error::Failed to parse JSON from Claude response", file=sys.stderr)
        print(f"Response text: {response_text[:500]}", file=sys.stderr)
        return {
            "functions_annotated": [],
            "summary": {"total_annotated": 0, "imports_added": []},
            "_metadata": {"error": "JSON parse error"},
        }
    except Exception as e:
        print(f"::error::API error: {e}", file=sys.stderr)
        return {
            "functions_annotated": [],
            "summary": {"total_annotated": 0, "imports_added": []},
            "_metadata": {"error": str(e)},
        }


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate cost based on Claude Haiku 4.5 pricing."""
    input_cost = (input_tokens / 1_000_000) * 1
    output_cost = (output_tokens / 1_000_000) * 5
    return round(input_cost + output_cost, 2)


def main() -> None:
    """Main entry point for type hints generation."""
    hints_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/missing-hints.json")
    output_path = Path(
        sys.argv[2] if len(sys.argv) > 2 else "/tmp/type-hints-suggestions.json"
    )

    print(f"Loading functions without type hints from {hints_path}...")
    functions = load_missing_hints(hints_path)

    if not functions:
        print("No functions need type hints. Skipping generation.")
        result = {
            "functions_annotated": [],
            "summary": {"total_annotated": 0, "imports_added": []},
            "_metadata": {
                "model": "N/A",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        }
    else:
        print(f"Generating type hints for {len(functions)} functions...")

        all_results = []
        total_input_tokens = 0
        total_output_tokens = 0

        batch_size = 10
        for i in range(0, len(functions), batch_size):
            batch = functions[i : i + batch_size]
            print(f"Processing batch {i // batch_size + 1} ({len(batch)} functions)...")

            batch_result = generate_type_hints_batch(batch)
            all_results.extend(batch_result.get("functions_annotated", []))

            metadata = batch_result.get("_metadata", {})
            total_input_tokens += metadata.get("input_tokens", 0)
            total_output_tokens += metadata.get("output_tokens", 0)

        all_imports = set()
        for func in all_results:
            all_imports.update(func.get("imports_needed", []))

        result = {
            "functions_annotated": all_results,
            "summary": {
                "total_annotated": len(all_results),
                "imports_added": sorted(all_imports),
            },
            "_metadata": {
                "model": "claude-haiku-4-5-20251001",
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            },
        }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Type hints generation complete. Results saved to {output_path}")

    summary = result.get("summary", {})
    metadata = result.get("_metadata", {})

    print("\n## Type Hints Generation Results\n")
    print(f"- **Functions annotated**: {summary.get('total_annotated', 0)}")
    print(f"- **Imports needed**: {len(summary.get('imports_added', []))}")

    if metadata.get("input_tokens"):
        cost = estimate_cost(metadata["input_tokens"], metadata["output_tokens"])
        print(f"\n**Cost**: ${cost}")
        print(
            f"**Tokens**: {metadata['total_tokens']:,} ({metadata['input_tokens']:,} in + {metadata['output_tokens']:,} out)"
        )

        github_output = os.environ.get("GITHUB_OUTPUT", "")
        if github_output:
            with open(github_output, "a") as f:
                f.write(f"type_hints_cost={cost}\n")


if __name__ == "__main__":
    main()
