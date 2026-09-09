#!/usr/bin/env python3
"""FastAPI server wrapper for Open Interpreter"""

import collections
import hmac
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, field_validator

app = FastAPI(title="Open Interpreter API")

LLM_API_URL = os.environ.get("LLM_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("OPEN_INTERPRETER_API_KEY", "")
AUTO_RUN = os.environ.get("OPEN_INTERPRETER_AUTO_RUN", "false").lower() == "true"
DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

MAX_MESSAGE_LENGTH = 32000

# A streamed run gets the same wall-clock budget as a blocking one.
STREAM_TIMEOUT_SECONDS = 300
# Grace period between SIGTERM and SIGKILL for a runner that will not exit.
TERMINATE_GRACE_SECONDS = 5
# Non-SSE runner output kept for the failure log, bounded so a chatty
# runner cannot grow this without limit.
STDERR_TAIL_LINES = 20

logger = logging.getLogger("open-interpreter")

security = HTTPBearer()


def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Verify the API key from the Authorization header."""
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="OPEN_INTERPRETER_API_KEY not configured",
        )
    # Compared as UTF-8 bytes: compare_digest raises TypeError on non-ASCII
    # str, which would turn an unauthenticated request into a 500 not a 401.
    if not hmac.compare_digest(
        credentials.credentials.encode("utf-8"), API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials


class ChatRequest(BaseModel):
    message: str
    stream: bool = True

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Message cannot be empty")
        if len(v) > MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"Message exceeds maximum length of {MAX_MESSAGE_LENGTH}"
            )
        # Reject control characters (allow newline, tab, carriage return)
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", v):
            raise ValueError("Message contains invalid control characters")
        return v


# Static runner scripts — message is passed via stdin as JSON, never interpolated
_RUNNER_SCRIPT = r"""
import json
import sys

config = json.loads(sys.stdin.read())

from interpreter import interpreter

interpreter.llm.model = "openai/x"
interpreter.llm.api_key = "fake_key"
interpreter.llm.api_base = config["llm_api_url"]
interpreter.auto_run = config["auto_run"]
interpreter.offline = True

result = interpreter.chat(config["message"], stream=False)

if isinstance(result, list):
    for msg in result:
        print(f"RESULT: {msg}")
else:
    print(f"RESULT: {result}")
"""

_STREAM_RUNNER_SCRIPT = r"""
import json
import sys

config = json.loads(sys.stdin.read())

from interpreter import interpreter

interpreter.llm.model = "openai/x"
interpreter.llm.api_key = "fake_key"
interpreter.llm.api_base = config["llm_api_url"]
interpreter.auto_run = config["auto_run"]
interpreter.offline = True

for chunk in interpreter.chat(config["message"], stream=True):
    print(f"SSE: {chunk}", flush=True)
"""


@app.get("/health")
def health():
    return {"status": "ok", "llm_url": LLM_API_URL}


@app.post("/chat")
def chat(req: ChatRequest, _auth=Depends(verify_api_key)):
    """Run Open Interpreter with a message and return output."""
    config = json.dumps({
        "message": req.message,
        "llm_api_url": LLM_API_URL,
        "auto_run": AUTO_RUN,
    })

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(_RUNNER_SCRIPT)
        script_path = f.name

    try:
        result = subprocess.run(
            ["python", script_path],
            input=config,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            logging.getLogger("open-interpreter").error(
                "Interpreter subprocess failed (exit %d): %s",
                result.returncode, result.stderr,
            )
            raise HTTPException(
                status_code=500,
                detail="Interpreter execution failed",
            )

        return {"output": result.stdout}

    finally:
        os.unlink(script_path)


def _cleanup_process(proc):
    """Stop proc if it is still running and close its pipes.

    Called from the generator's finally block, which also runs when the client
    disconnects mid-stream and the generator is closed.
    """
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("Runner ignored SIGTERM after %ds; killing", TERMINATE_GRACE_SECONDS)
            proc.kill()
            proc.wait()

    for stream in (proc.stdin, proc.stdout):
        if stream is not None and not stream.closed:
            stream.close()


def _stream_interpreter(script_path, config):
    """Yield SSE frames from the runner subprocess.

    The subprocess, its pipes and the temp script are released on every exit
    path, including a client disconnect (which closes this generator) and a
    runner that never terminates on its own.
    """
    try:
        proc = subprocess.Popen(
            ["python", script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError:
        os.unlink(script_path)
        raise

    # The read loop below blocks indefinitely on a silent pipe, so the deadline
    # has to be enforced from outside it.
    watchdog = threading.Timer(STREAM_TIMEOUT_SECONDS, proc.kill)
    watchdog.start()
    tail = collections.deque(maxlen=STDERR_TAIL_LINES)

    try:
        proc.stdin.write(config)
        proc.stdin.close()

        for line in proc.stdout:
            if line.startswith("SSE: "):
                yield f"data: {line[5:]}\n\n"
            else:
                tail.append(line.rstrip("\n"))

        returncode = proc.wait()
        if returncode != 0:
            # Headers are already sent, so the status code cannot change here;
            # signal the failure in-band instead of ending the stream silently.
            logger.error(
                "Interpreter runner failed (exit %d): %s",
                returncode, " | ".join(tail),
            )
            yield 'event: error\ndata: {"error": "Interpreter execution failed"}\n\n'
    finally:
        watchdog.cancel()
        _cleanup_process(proc)
        os.unlink(script_path)


@app.post("/chat/stream")
def chat_stream(req: ChatRequest, _auth=Depends(verify_api_key)):
    """Stream Open Interpreter output."""
    config = json.dumps({
        "message": req.message,
        "llm_api_url": LLM_API_URL,
        "auto_run": AUTO_RUN,
    })

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(_STREAM_RUNNER_SCRIPT)
        script_path = f.name

    return StreamingResponse(
        _stream_interpreter(script_path, config),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
