#!/usr/bin/env python3
"""
M7 Agent Template Validation.

Runs live prompt checks against a local OpenAI-compatible llama-server when it is
available. By default, missing local model infrastructure is reported as SKIP so
cold static audits do not fail because no Dream Server stack is running. Set
DREAM_REQUIRE_AGENT_TEMPLATE_SERVER=1 to make that condition a hard failure.
"""

from __future__ import annotations

import os
import sys
import time

import requests


LLAMA_SERVER_URL = os.environ.get("DREAM_AGENT_TEMPLATE_BASE_URL", "http://localhost:8080")
MODEL = os.environ.get("DREAM_AGENT_TEMPLATE_MODEL", "qwen2.5-32b-instruct")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


TEMPLATES = {
    "code-assistant": {
        "system": "You are an expert programming assistant. Write clean, well-documented code.",
        "tests": [
            "Write a Python function to calculate factorial",
            "Debug this: for i in range(len(items)): print(items[i])",
        ],
    },
    "research-assistant": {
        "system": "You are a research assistant. Provide factual, well-sourced information.",
        "tests": [
            "Summarize what Python list comprehensions are",
            "Explain the difference between a stack and a queue",
        ],
    },
    "data-analyst": {
        "system": "You are a data analysis assistant. Help process and understand data.",
        "tests": [
            "How would you find the average of a list of numbers in Python?",
            "Explain what pandas DataFrame.describe() does",
        ],
    },
    "writing-assistant": {
        "system": "You are a writing assistant. Improve clarity and fix errors.",
        "tests": [
            "Fix the grammar: 'Their going to the store'",
            "Make this more concise: 'Due to the fact that it was raining, we decided to stay inside'",
        ],
    },
    "system-admin": {
        "system": "You are a system administration assistant. Help with Docker and Linux.",
        "tests": [
            "What command shows running Docker containers?",
            "How do you check disk usage on Linux?",
        ],
    },
}


def server_available() -> bool:
    """Return True when a local OpenAI-compatible model endpoint is reachable."""
    try:
        response = requests.get(f"{LLAMA_SERVER_URL}/v1/models", timeout=2)
        return response.status_code < 500
    except requests.RequestException:
        return False


def test_template(name: str, config: dict) -> dict:
    """Test a single template."""
    print(f"\n[TEST] {name}")

    results = {
        "template": name,
        "tests": [],
        "passed": 0,
        "failed": 0,
    }

    for test_prompt in config["tests"]:
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": config["system"]},
                {"role": "user", "content": test_prompt},
            ],
            "max_tokens": 200,
            "temperature": 0.7,
        }

        try:
            start = time.time()
            response = requests.post(
                f"{LLAMA_SERVER_URL}/v1/chat/completions",
                json=payload,
                timeout=30,
            )
            elapsed = (time.time() - start) * 1000

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # Basic validation: response should be non-empty and bounded.
                passed = 50 < len(content) < 2000

                results["tests"].append(
                    {
                        "prompt": test_prompt[:50],
                        "passed": passed,
                        "time_ms": elapsed,
                        "response_preview": content[:100],
                    }
                )

                if passed:
                    results["passed"] += 1
                    print(f"  [PASS] {test_prompt[:40]}... ({elapsed:.0f}ms)")
                else:
                    results["failed"] += 1
                    print(f"  [FAIL] {test_prompt[:40]}... (empty or too long)")
            else:
                results["tests"].append(
                    {
                        "prompt": test_prompt[:50],
                        "passed": False,
                        "error": f"HTTP {response.status_code}",
                    }
                )
                results["failed"] += 1
                print(f"  [FAIL] {test_prompt[:40]}... (HTTP {response.status_code})")

        except Exception as exc:  # noqa: BLE001 - diagnostic script should continue
            results["tests"].append(
                {
                    "prompt": test_prompt[:50],
                    "passed": False,
                    "error": str(exc),
                }
            )
            results["failed"] += 1
            print(f"  [FAIL] {test_prompt[:40]}... ({exc})")

    return results


def main() -> int:
    print("=" * 60)
    print("M7 Agent Template Validation")
    print(f"Testing {MODEL} at {LLAMA_SERVER_URL}")
    print("=" * 60)

    if not server_available():
        message = f"LLM server unavailable at {LLAMA_SERVER_URL}"
        if os.environ.get("DREAM_REQUIRE_AGENT_TEMPLATE_SERVER") == "1":
            print(f"[FAIL] {message}")
            return 1
        print(f"[SKIP] {message}")
        print("Set DREAM_REQUIRE_AGENT_TEMPLATE_SERVER=1 to make this a hard failure.")
        return 0

    all_results = []
    total_passed = 0
    total_failed = 0

    for name, config in TEMPLATES.items():
        result = test_template(name, config)
        all_results.append(result)
        total_passed += result["passed"]
        total_failed += result["failed"]

    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    for result in all_results:
        if result["failed"] == 0:
            status = "[PASS]"
        elif result["passed"] > 0:
            status = "[PARTIAL]"
        else:
            status = "[FAIL]"
        total = result["passed"] + result["failed"]
        print(f"{result['template']:20} {status} ({result['passed']}/{total} tests)")

    print("-" * 60)
    print(f"Total: {total_passed} passed, {total_failed} failed")

    if total_failed == 0:
        print("\n[PASS] All templates validated successfully!")
        return 0

    print(f"\n[FAIL] {total_failed} tests failed - review needed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
