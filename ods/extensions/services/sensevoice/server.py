"""OpenAI-compatible speech-to-text server backed by FunASR / SenseVoice.

Exposes:
  GET  /health                     -> {"status": "ok"}
  POST /v1/audio/transcriptions    -> {"text": "..."}   (OpenAI Whisper-compatible)

The FunASR model is built lazily on first transcription so this module can be
imported (and unit-tested) without funasr/torch installed.
"""
import os
import re
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

MODEL_ID = "iic/SenseVoiceSmall"
VAD_MODEL_ID = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"

MAX_AUDIO_BYTES = int(os.environ.get("SENSEVOICE_MAX_AUDIO_BYTES", 25 * 1024 * 1024))
_READ_CHUNK_BYTES = 1024 * 1024

app = FastAPI(title="ODS SenseVoice STT")

_model = None

# SenseVoice emits markup tokens like <|en|><|EMO_UNKNOWN|><|Speech|><|woitn|>
# around the transcription. Strip them to return clean text.
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]*\|>")


def strip_special_tokens(text: str) -> str:
    """Remove SenseVoice markup tokens, returning clean transcription text."""
    return _SPECIAL_TOKEN_RE.sub("", text).strip()


def get_model():
    """Build the FunASR model once. Heavy imports stay inside this function so
    the module imports without funasr/torch present."""
    global _model
    if _model is None:
        from funasr import AutoModel

        _model = AutoModel(
            model=MODEL_ID,
            vad_model=VAD_MODEL_ID,
            vad_kwargs={"max_single_segment_time": 30000},
            device=os.environ.get("SENSEVOICE_DEVICE", "cpu"),
            trust_remote_code=True,
        )
    return _model


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: Optional[UploadFile] = File(default=None),
    language: str = Form(default="auto"),
    model: Optional[str] = Form(default=None),
    response_format: Optional[str] = Form(default=None),
    temperature: Optional[str] = Form(default=None),
):
    if file is None:
        raise HTTPException(status_code=400, detail="no audio file provided")

    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        total = 0
        while True:
            chunk = await file.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_AUDIO_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"audio upload exceeds {MAX_AUDIO_BYTES} byte limit",
                )
            tmp.write(chunk)
        tmp.flush()
        result = get_model().generate(
            input=tmp.name,
            cache={},
            language=language,
            use_itn=True,
            batch_size_s=0,
            merge_vad=True,
        )

    # merge_vad=True normally yields a single entry, but join defensively so a
    # multi-segment result for long audio is not silently truncated.
    text = strip_special_tokens(" ".join(item["text"] for item in result)) if result else ""
    return {"text": text}
