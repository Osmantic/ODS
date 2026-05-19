"""
Bark TTS API Server
Wraps suno-ai/bark with a minimal FastAPI HTTP interface.
Compatible with the Dream Server extensions ecosystem.
"""

import os
import io
import base64
import logging
import threading
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bark-server")

app = FastAPI(title="Bark TTS API", version="0.1.6")

# Global model state — loaded on first request to avoid startup delay
_models_loaded = False
_model_lock = threading.Lock()

# Thread pool for CPU-intensive TTS generation
_executor = ThreadPoolExecutor(max_workers=2)

# Valid output formats
VALID_FORMATS = {"WAV", "MP3", "OGG", "FLAC"}

# Max text length to prevent DoS via memory exhaustion
MAX_TEXT_LENGTH = 10000

# Valid Bark voice presets (exact match: vX/xx_speaker_N)
VALID_VOICES = {
    "v2/en_speaker_0", "v2/en_speaker_1", "v2/en_speaker_2", "v2/en_speaker_3",
    "v2/en_speaker_4", "v2/en_speaker_5", "v2/en_speaker_6", "v2/en_speaker_7",
    "v2/en_speaker_8", "v2/en_speaker_9",
    "v2/de_speaker_0", "v2/de_speaker_1", "v2/de_speaker_2", "v2/de_speaker_3",
    "v2/de_speaker_4", "v2/de_speaker_5", "v2/de_speaker_6", "v2/de_speaker_7",
    "v2/de_speaker_8", "v2/de_speaker_9",
    "v2/es_speaker_0", "v2/es_speaker_1", "v2/es_speaker_2", "v2/es_speaker_3",
    "v2/es_speaker_4", "v2/es_speaker_5", "v2/es_speaker_6", "v2/es_speaker_7",
    "v2/es_speaker_8", "v2/es_speaker_9",
    "v2/fr_speaker_0", "v2/fr_speaker_1", "v2/fr_speaker_2", "v2/fr_speaker_3",
    "v2/fr_speaker_4", "v2/fr_speaker_5", "v2/fr_speaker_6", "v2/fr_speaker_7",
    "v2/fr_speaker_8", "v2/fr_speaker_9",
    "v2/hi_speaker_0", "v2/hi_speaker_1", "v2/hi_speaker_2", "v2/hi_speaker_3",
    "v2/hi_speaker_4", "v2/hi_speaker_5", "v2/hi_speaker_6", "v2/hi_speaker_7",
    "v2/hi_speaker_8", "v2/hi_speaker_9",
    "v2/it_speaker_0", "v2/it_speaker_1", "v2/it_speaker_2", "v2/it_speaker_3",
    "v2/it_speaker_4", "v2/it_speaker_5", "v2/it_speaker_6", "v2/it_speaker_7",
    "v2/it_speaker_8", "v2/it_speaker_9",
    "v2/ja_speaker_0", "v2/ja_speaker_1", "v2/ja_speaker_2", "v2/ja_speaker_3",
    "v2/ja_speaker_4", "v2/ja_speaker_5", "v2/ja_speaker_6", "v2/ja_speaker_7",
    "v2/ja_speaker_8", "v2/ja_speaker_9",
    "v2/ko_speaker_0", "v2/ko_speaker_1", "v2/ko_speaker_2", "v2/ko_speaker_3",
    "v2/ko_speaker_4", "v2/ko_speaker_5", "v2/ko_speaker_6", "v2/ko_speaker_7",
    "v2/ko_speaker_8", "v2/ko_speaker_9",
    "v2/pl_speaker_0", "v2/pl_speaker_1", "v2/pl_speaker_2", "v2/pl_speaker_3",
    "v2/pl_speaker_4", "v2/pl_speaker_5", "v2/pl_speaker_6", "v2/pl_speaker_7",
    "v2/pl_speaker_8", "v2/pl_speaker_9",
    "v2/pt_speaker_0", "v2/pt_speaker_1", "v2/pt_speaker_2", "v2/pt_speaker_3",
    "v2/pt_speaker_4", "v2/pt_speaker_5", "v2/pt_speaker_6", "v2/pt_speaker_7",
    "v2/pt_speaker_8", "v2/pt_speaker_9",
    "v2/ru_speaker_0", "v2/ru_speaker_1", "v2/ru_speaker_2", "v2/ru_speaker_3",
    "v2/ru_speaker_4", "v2/ru_speaker_5", "v2/ru_speaker_6", "v2/ru_speaker_7",
    "v2/ru_speaker_8", "v2/ru_speaker_9",
    "v2/tr_speaker_0", "v2/tr_speaker_1", "v2/tr_speaker_2", "v2/tr_speaker_3",
    "v2/tr_speaker_4", "v2/tr_speaker_5", "v2/tr_speaker_6", "v2/tr_speaker_7",
    "v2/tr_speaker_8", "v2/tr_speaker_9",
    "v2/zh_speaker_0", "v2/zh_speaker_1", "v2/zh_speaker_2", "v2/zh_speaker_3",
    "v2/zh_speaker_4", "v2/zh_speaker_5", "v2/zh_speaker_6", "v2/zh_speaker_7",
    "v2/zh_speaker_8", "v2/zh_speaker_9",
}


def _load_models():
    global _models_loaded
    # Double-checked locking pattern to prevent race conditions
    if not _models_loaded:
        with _model_lock:
            if not _models_loaded:
                logger.info("Loading Bark models (first request — may take a few minutes)...")
                from bark import preload_models
                preload_models()
                _models_loaded = True
                logger.info("Bark models loaded.")


class TTSRequest(BaseModel):
    text: str = Field(..., max_length=MAX_TEXT_LENGTH, description="Text to synthesize (max 10K chars)")
    voice_preset: Optional[str] = "v2/en_speaker_6"
    output_format: Optional[str] = "wav"  # wav, mp3, ogg, flac

    @field_validator("output_format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        fmt = v.upper()
        if fmt not in VALID_FORMATS:
            raise ValueError(f"Invalid format '{v}'. Must be one of: {', '.join(VALID_FORMATS)}")
        return fmt

    @field_validator("voice_preset")
    @classmethod
    def validate_voice_preset(cls, v: str) -> str:
        if not v:
            return v
        if v not in VALID_VOICES:
            raise ValueError(f"Invalid voice_preset '{v}'. Use format 'vX/xx_speaker_N' (e.g., v2/en_speaker_6). See /voices for list.")
        return v


class TTSResponse(BaseModel):
    audio_base64: str
    sample_rate: int
    format: str


def _generate_audio_sync(text: str, voice_preset: str, output_format: str) -> dict:
    """Synchronous audio generation — runs in thread pool."""
    from bark import generate_audio, SAMPLE_RATE

    _load_models()
    audio_array = generate_audio(text, history_prompt=voice_preset)

    buf = io.BytesIO()
    sf.write(buf, audio_array, SAMPLE_RATE, format=output_format)
    buf.seek(0)
    audio_b64 = base64.b64encode(buf.read()).decode("utf-8")

    return {
        "audio_base64": audio_b64,
        "sample_rate": SAMPLE_RATE,
        "format": output_format.lower(),
    }


@app.get("/health")
def health():
    return {"status": "ok", "models_loaded": _models_loaded}


@app.post("/tts", response_model=TTSResponse)
def text_to_speech(req: TTSRequest):
    """
    Generate speech audio from text using Bark.

    Args:
        text: The text to synthesize (supports [laughter], [sighs], etc.)
        voice_preset: Bark voice preset (e.g. v2/en_speaker_6)
        output_format: wav, mp3, ogg, or flac

    Returns:
        Base64-encoded audio with sample rate.
    """
    try:
        # Run CPU-intensive TTS in thread pool to prevent worker starvation
        future = _executor.submit(
            _generate_audio_sync,
            req.text,
            req.voice_preset,
            req.output_format,
        )
        result = future.result()

        return TTSResponse(**result)
    except ValueError as e:
        # Validation errors — safe to expose
        logger.warning(f"TTS validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Internal errors — log full trace, return generic message
        logger.exception(f"TTS generation failed: {e}")
        raise HTTPException(status_code=500, detail="TTS generation failed. Please try again.")


@app.post("/tts/stream")
def text_to_speech_stream(req: TTSRequest):
    """
    Generate speech and return raw audio bytes (wav).
    Suitable for streaming to audio players.
    """
    try:
        future = _executor.submit(
            _generate_audio_stream_sync,
            req.text,
            req.voice_preset,
        )
        return future.result()
    except ValueError as e:
        logger.warning(f"TTS stream validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"TTS stream failed: {e}")
        raise HTTPException(status_code=500, detail="TTS generation failed. Please try again.")


def _generate_audio_stream_sync(text: str, voice_preset: str) -> Response:
    """Synchronous stream generation — runs in thread pool."""
    from bark import generate_audio, SAMPLE_RATE

    _load_models()
    audio_array = generate_audio(text, history_prompt=voice_preset)

    buf = io.BytesIO()
    sf.write(buf, audio_array, SAMPLE_RATE, format="WAV")
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="audio/wav",
        headers={"Content-Disposition": "attachment; filename=bark_output.wav"},
    )


@app.get("/voices")
def list_voices():
    """List available Bark voice presets."""
    voices = {
        "english": [f"v2/en_speaker_{i}" for i in range(10)],
        "german": [f"v2/de_speaker_{i}" for i in range(10)],
        "spanish": [f"v2/es_speaker_{i}" for i in range(10)],
        "french": [f"v2/fr_speaker_{i}" for i in range(10)],
        "hindi": [f"v2/hi_speaker_{i}" for i in range(10)],
        "italian": [f"v2/it_speaker_{i}" for i in range(10)],
        "japanese": [f"v2/ja_speaker_{i}" for i in range(10)],
        "korean": [f"v2/ko_speaker_{i}" for i in range(10)],
        "polish": [f"v2/pl_speaker_{i}" for i in range(10)],
        "portuguese": [f"v2/pt_speaker_{i}" for i in range(10)],
        "russian": [f"v2/ru_speaker_{i}" for i in range(10)],
        "turkish": [f"v2/tr_speaker_{i}" for i in range(10)],
        "chinese": [f"v2/zh_speaker_{i}" for i in range(10)],
    }
    return voices
