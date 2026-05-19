"""Bark TTS API Server Tests"""

import pytest
import base64
import io
import threading
import numpy as np
from unittest.mock import patch, MagicMock, call
from fastapi.testclient import TestClient
from fastapi import HTTPException

# Import the module under test
import server

client = TestClient(server.app)


# Fixtures
@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    # Use setattr to modify the module's attribute, not a local variable
    original = server._models_loaded
    server._models_loaded = False
    yield
    server._models_loaded = original


@pytest.fixture
def mock_bark_generate_audio():
    """Mock bark.generate_audio to return a simple audio array."""
    with patch("bark.generate_audio") as mock:
        # Generate a simple 1-second audio array at 24kHz (numpy array, not list)
        mock.return_value = np.array([0.1] * 24000, dtype=np.float32)
        yield mock


@pytest.fixture
def mock_bark_preload_models():
    """Mock bark.preload_models."""
    with patch("bark.preload_models") as mock:
        yield mock


@pytest.fixture
def mock_soundfile_write():
    """Mock soundfile.write."""
    with patch("server.sf.write") as mock:
        # Make it write to the provided buffer
        def side_effect(buf, audio_array, sample_rate, format=None):
            # Simulate writing by just seeking to end
            buf.seek(0)
            buf.write(b'\x00' * 100)  # fake WAV data
            buf.seek(0)
        mock.side_effect = side_effect
        yield mock


@pytest.fixture
def mock_soundfile_read():
    """Mock soundfile.read for stream endpoint."""
    with patch("server.sf.read") as mock:
        mock.return_value = (np.array([0.1] * 24000, dtype=np.float32), 24000)
        yield mock


# Tests for /health endpoint
def test_health_initial():
    """Test health endpoint before models are loaded."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["models_loaded"] is False


def test_health_after_load(mock_bark_preload_models):
    """Test health endpoint after models are loaded."""
    # Trigger model loading
    server._load_models()
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["models_loaded"] is True


# Tests for /tts endpoint
def test_tts_success(mock_bark_generate_audio, mock_soundfile_write):
    """Test successful TTS request."""
    with patch("server._models_loaded", True):
        response = client.post("/tts", json={
            "text": "Hello, world!",
            "voice_preset": "v2/en_speaker_6",
            "output_format": "wav"
        })
        assert response.status_code == 200
        data = response.json()
        assert "audio_base64" in data
        assert data["sample_rate"] == 24000
        assert data["format"] == "wav"
        assert base64.b64decode(data["audio_base64"])


def test_tts_default_format(mock_bark_generate_audio, mock_soundfile_write):
    """Test TTS with default format (wav)."""
    with patch("server._models_loaded", True):
        response = client.post("/tts", json={
            "text": "Hello, world!",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["format"] == "wav"


def test_tts_case_insensitive_format(mock_bark_generate_audio, mock_soundfile_write):
    """Test TTS with lowercase format."""
    with patch("server._models_loaded", True):
        response = client.post("/tts", json={
            "text": "Hello, world!",
            "output_format": "mp3"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["format"] == "mp3"


def test_tts_invalid_format():
    """Test TTS with invalid format."""
    response = client.post("/tts", json={
        "text": "Hello, world!",
        "output_format": "avi"
    })
    assert response.status_code == 422
    assert "output_format" in response.json()["detail"].lower()


def test_tts_text_too_long():
    """Test TTS with text exceeding MAX_TEXT_LENGTH."""
    long_text = "a" * (server.MAX_TEXT_LENGTH + 1)
    response = client.post("/tts", json={
        "text": long_text
    })
    assert response.status_code == 422
    assert "text" in response.json()["detail"].lower()


def test_tts_text_empty():
    """Test TTS with empty text."""
    response = client.post("/tts", json={
        "text": ""
    })
    assert response.status_code == 200  # Empty text is allowed by Pydantic


def test_tts_model_loading_on_first_request(mock_bark_preload_models, mock_bark_generate_audio, mock_soundfile_write):
    """Test that models are loaded on first request."""
    # Ensure models are not loaded
    with patch("server._models_loaded", False):
        response = client.post("/tts", json={
            "text": "Hello, world!",
        })
        assert response.status_code == 200
        # Verify preload_models was called
        mock_bark_preload_models.assert_called_once()


def test_tts_concurrent_requests(mock_bark_preload_models, mock_bark_generate_audio, mock_soundfile_write):
    """Test concurrent TTS requests."""
    with patch("server._models_loaded", False):
        # Make multiple concurrent requests
        threads = []
        results = []

        def make_request():
            try:
                resp = client.post("/tts", json={"text": "Hello!"})
                results.append(resp.status_code)
            except Exception as e:
                results.append(str(e))

        for _ in range(3):
            t = threading.Thread(target=make_request)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All requests should succeed
        assert all(code == 200 for code in results)
        # preload_models should be called exactly once
        assert mock_bark_preload_models.call_count == 1


# Tests for /tts/stream endpoint
def test_tts_stream_success(mock_bark_generate_audio, mock_soundfile_write):
    """Test successful TTS stream request."""
    with patch("server._models_loaded", True):
        response = client.post("/tts/stream", json={
            "text": "Hello, world!",
            "voice_preset": "v2/en_speaker_6"
        })
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert "bark_output.wav" in response.headers["content-disposition"]
        assert len(response.content) > 0


def test_tts_stream_default_format(mock_bark_generate_audio, mock_soundfile_write):
    """Test TTS stream always returns WAV regardless of format."""
    with patch("server._models_loaded", True):
        response = client.post("/tts/stream", json={
            "text": "Hello, world!",
            "output_format": "mp3"  # This should be ignored for stream endpoint
        })
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"


# Tests for validation
def test_tts_invalid_voice_preset():
    """Test TTS with invalid voice preset (now rejected by server validator)."""
    response = client.post("/tts", json={
        "text": "Hello, world!",
        "voice_preset": "invalid_preset"
    })
    assert response.status_code == 422
    assert "voice_preset" in response.json()["detail"].lower()


def test_tts_text_max_length_boundary():
    """Test TTS with text at MAX_TEXT_LENGTH boundary."""
    text = "a" * server.MAX_TEXT_LENGTH
    response = client.post("/tts", json={
        "text": text
    })
    assert response.status_code == 200


# Tests for error handling
def test_tts_generation_error(mock_bark_generate_audio):
    """Test TTS when bark.generate_audio raises an exception."""
    mock_bark_generate_audio.side_effect = Exception("Bark error")
    with patch("server._models_loaded", True):
        response = client.post("/tts", json={
            "text": "Hello, world!",
        })
        assert response.status_code == 500
        assert "TTS generation failed" in response.json()["detail"]


def test_tts_stream_generation_error(mock_bark_generate_audio):
    """Test TTS stream when bark.generate_audio raises an exception."""
    mock_bark_generate_audio.side_effect = Exception("Bark error")
    with patch("server._models_loaded", True):
        response = client.post("/tts/stream", json={
            "text": "Hello, world!",
        })
        assert response.status_code == 500
        assert "TTS generation failed" in response.json()["detail"]


def test_load_models_thread_safety(mock_bark_preload_models):
    """Test that model loading is thread-safe."""
    # Reset global state
    server._models_loaded = False

    results = []
    errors = []

    def load_and_check():
        try:
            server._load_models()
            results.append(server._models_loaded)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=load_and_check) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 10
    assert all(results)
    # preload_models should only be called once due to lock
    assert mock_bark_preload_models.call_count == 1
