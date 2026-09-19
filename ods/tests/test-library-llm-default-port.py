#!/usr/bin/env python3
"""Regression: library extension compose defaults must reach llama-server.

The core llama-server container publishes its OpenAI-compatible API on
container port 8080 (see docker-compose.base.yml). Library compose files that
default LLM_API_URL/OPENAI_API_BASE-style variables to a different port leave
freshly enabled extensions unable to reach the model at all (issue #5324).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "docker-compose.base.yml"
LIBRARY = ROOT / "extensions" / "library" / "services"


def _llama_server_container_port() -> int:
    """Parse the port llama-server listens on inside the compose network."""
    text = BASE_COMPOSE.read_text(encoding="utf-8")
    match = re.search(r"llama-server:.*?- \"(\d+)\"", text, re.DOTALL)
    assert match, "could not find llama-server exposed port in base compose"
    return int(match.group(1))


def test_library_llm_defaults_match_llama_server_port():
    expected = _llama_server_container_port()
    offenders = []
    for compose in sorted(LIBRARY.glob("*/compose*.yaml")):
        for lineno, line in enumerate(
            compose.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for hit in re.finditer(r"llama-server:(\d+)", line):
                if int(hit.group(1)) != expected:
                    offenders.append(f"{compose.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "library compose defaults point at a llama-server port the container "
        "does not listen on:\n" + "\n".join(offenders)
    )


if __name__ == "__main__":
    test_library_llm_defaults_match_llama_server_port()
    print("PASS: library LLM URL defaults match the llama-server port")
