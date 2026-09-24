"""Run inside the built AudioCraft image; model weights are deliberately omitted."""

import json
import os
import runpy
import wave
from pathlib import Path
from unittest.mock import patch

import gradio as gr
import torch
from audiocraft.models import AudioGen, MusicGen
from fastapi.testclient import TestClient


class FixtureModel:
    sample_rate = 32000

    def set_generation_params(self, **values):
        self.parameters = values

    def generate(self, descriptions):
        assert descriptions == ["Local fixture"]
        return torch.zeros((1, 1, 3200))


def main():
    assert os.getuid() == 1000
    with patch.object(MusicGen, "get_pretrained", return_value=FixtureModel()), \
            patch.object(AudioGen, "get_pretrained", return_value=FixtureModel()):
        namespace = runpy.run_path("/app/app.py")
    app = gr.routes.App.create_app(namespace["demo"])
    with TestClient(app) as client:
        for path in ("/", "/config", "/info"):
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)
            print(json.dumps({"path": path, "status": response.status_code}), flush=True)
    for operation in ("generate_music", "generate_sound"):
        output = Path(namespace[operation]("Local fixture", 1, 100, 1.0))
        with wave.open(str(output)) as audio:
            assert audio.getframerate() == 32000
            assert audio.getnframes() == 3200
        output.unlink()
        print(json.dumps({"operation": operation, "wav_frames": 3200}), flush=True)


if __name__ == "__main__":
    main()
