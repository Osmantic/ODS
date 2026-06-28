#!/usr/bin/env python3
"""
Pre-flight model validation for ODS offline mode.
Ensures required models are downloaded before starting services.
"""

import sys
from pathlib import Path

# Model requirements for offline mode
REQUIRED_MODELS = {
    "llm": {
        "path": "data/models",
        "description": "Primary LLM (GGUF model)",
        "size_gb": 4,
    },
    "whisper": {
        "path": "data/whisper/faster-whisper-base",
        "description": "Whisper STT model (base)",
        "size_gb": 0.15,
    },
    "kokoro": {
        "path": "data/kokoro/voices/af_heart.pt",
        "description": "Kokoro TTS voice (af_heart)",
        "size_gb": 0.3,
    },
    "embeddings": {
        "path": "data/embeddings/BAAI/bge-base-en-v1.5",
        "description": "Embedding model (BGE base)",
        "size_gb": 0.4,
    },
}

def check_model(service, config):
    """Check if a model exists and has reasonable size."""
    # Resolve base path relative to script location (scripts/ -> parent -> ods root)
    base_path = Path(__file__).parent.parent
    model_path = base_path / config["path"]

    if not model_path.exists():
        return False, f"Not found: {config['path']}"

    # Check size (rough validation)
    if model_path.is_file():
        size_gb = model_path.stat().st_size / (1024**3)
    else:
        # Directory - sum all files
        size_gb = sum(f.stat().st_size for f in model_path.rglob('*') if f.is_file()) / (1024**3)

    min_size = config["size_gb"] * 0.5  # At least 50% of expected size
    if size_gb < min_size:
        return False, f"Too small: {size_gb:.2f}GB (expected ~{config['size_gb']}GB)"

    return True, f"OK: {size_gb:.2f}GB"

def main():
    """Validate all required models are present."""
    print("=" * 60)
    print("ODS Offline Mode - Model Validation")
    print("=" * 60)

    all_ok = True
    missing = []

    for service, config in REQUIRED_MODELS.items():
        ok, msg = check_model(service, config)
        status = "✓" if ok else "✗"
        print(f"{status} {config['description']:40s} {msg}")

        if not ok:
            all_ok = False
            missing.append(service)

    print("=" * 60)

    if all_ok:
        print("All models present. Ready for offline mode!")
        return 0
    else:
        print(f"\nMISSING MODELS: {', '.join(missing)}")
        print("\nDownload models before starting offline mode:")
        print("  ./scripts/download-models.sh")
        return 1

if __name__ == "__main__":
    sys.exit(main())
