#!/usr/bin/env python3
"""
Docstring Generator using Claude Haiku 4.5

Generates Google-style docstrings for functions missing documentation.
Processes in batches of 8 functions per API call for cost efficiency.

Cost: $20-35 per run (depending on function count and complexity)
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

from anthropic_helper import create_message


def load_missing_docs(docs_path: Path) -> list[dict[str, Any]]:
    """Load functions without docstrings from JSON."""
    try:
        with open(docs_path, "r") as f:
            docs = json.load(f)
        return docs
    except FileNotFoundError:
        print(f"::error::Docs file not found: {docs_path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"::error::Invalid JSON in docs file: {docs_path}", file=sys.stderr)
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


def build_docstring_prompt(functions: list[dict[str, Any]]) -> str:
    """Build the prompt for generating docstrings."""
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

    return f"""You are a Python documentation expert. Generate clear, helpful Google-style docstrings for functions missing documentation.

## Functions to Document ({len(functions)} total)

{functions_text}

## Your Task

For each function, provide a complete Google-style docstring including:
1. **Summary line** - One sentence describing what the function does
2. **Args section** - Document each parameter with type and description
3. **Returns section** - Document return value with type and description
4. **Raises section** - Document exceptions raised (if applicable)
5. **Examples section** (optional) - Usage examples for complex functions

## Guidelines

- Summary line: Start with imperative verb (e.g., "Calculate", "Return", "Process")
- Be concise but informative
- Don't repeat the function name in the summary
- Use present tense for descriptions
- Include type information in Args/Returns even if type hints exist
- Only include Raises section if function actually raises exceptions
- Only include Example section for non-trivial functions

## Output Format

Respond with JSON:

```json
{{
  "functions_documented": [
    {{
      "file": "app/example.py",
      "function": "process_data",
      "line": 42,
      "docstring": "Process data items and return results.\\n\\nArgs:\\n    data: List of items to process.\\n    options: Optional configuration dict.\\n\\nReturns:\\n    Processed results as dict."
    }}
  ],
  "summary": {{
    "total_documented": 5,
    "functions_with_examples": 2,
    "functions_with_raises": 3
  }}
}}
```

Begin your analysis."""


def generate_docstrings_batch(
    functions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate docstrings for a batch of functions using Claude Haiku 4.5."""
    prompt = build_docstring_prompt(functions)

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
            "functions_documented": [],
            "summary": {"total_documented": 0},
            "_metadata": {"error": "JSON parse error"},
        }
    except Exception as e:
        print(f"::error::API error: {e}", file=sys.stderr)
        return {
            "functions_documented": [],
            "summary": {"total_documented": 0},
            "_metadata": {"error": str(e)},
        }


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate cost based on Claude Haiku 4.5 pricing."""
    input_cost = (input_tokens / 1_000_000) * 1
    output_cost = (output_tokens / 1_000_000) * 5
    return round(input_cost + output_cost, 2)


def main() -> None:
    """Main entry point for docstring generation."""
    docs_path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/missing-docs.json")
    output_path = Path(
        sys.argv[2] if len(sys.argv) > 2 else "/tmp/documentation-suggestions.json"
    )

    print(f"Loading functions without docstrings from {docs_path}...")
    functions = load_missing_docs(docs_path)

    if not functions:
        print("No functions need docstrings. Skipping generation.")
        result = {
            "functions_documented": [],
            "summary": {"total_documented": 0},
            "_metadata": {
                "model": "N/A",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        }
    else:
        print(f"Generating docstrings for {len(functions)} functions...")

        all_results = []
        total_input_tokens = 0
        total_output_tokens = 0

        batch_size = 8
        for i in range(0, len(functions), batch_size):
            batch = functions[i : i + batch_size]
            print(f"Processing batch {i // batch_size + 1} ({len(batch)} functions)...")

            batch_result = generate_docstrings_batch(batch)
            all_results.extend(batch_result.get("functions_documented", []))

            metadata = batch_result.get("_metadata", {})
            total_input_tokens += metadata.get("input_tokens", 0)
            total_output_tokens += metadata.get("output_tokens", 0)

        result = {
            "functions_documented": all_results,
            "summary": {"total_documented": len(all_results)},
            "_metadata": {
                "model": "claude-haiku-4-5-20251001",
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            },
        }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Docstring generation complete. Results saved to {output_path}")

    summary = result.get("summary", {})
    metadata = result.get("_metadata", {})

    print("\n## Documentation Generation Results\n")
    print(f"- **Functions documented**: {summary.get('total_documented', 0)}")

    if metadata.get("input_tokens"):
        cost = estimate_cost(metadata["input_tokens"], metadata["output_tokens"])
        print(f"\n**Cost**: ${cost}")
        print(
            f"**Tokens**: {metadata['total_tokens']:,} ({metadata['input_tokens']:,} in + {metadata['output_tokens']:,} out)"
        )

        github_output = os.environ.get("GITHUB_OUTPUT", "")
        if github_output:
            with open(github_output, "a") as f:
                f.write(f"docstring_cost={cost}\n")


if __name__ == "__main__":
    main()
