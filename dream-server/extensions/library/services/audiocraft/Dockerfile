FROM python:3.10-slim-bookworm@sha256:4efa69bf17cfbd5eb44b170e40bb8091b1c0fadf3833aeb87b655512e24268e5

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install AudioCraft and dependencies
RUN pip install --no-cache-dir \
    audiocraft==1.3.0 \
    gradio==4.44.0 \
    torch==2.3.0 \
    torchaudio==2.3.0

# Set working directory
WORKDIR /app

# Create simple Gradio app for MusicGen
RUN cat > /app/app.py << 'EOF'
import gradio as gr
from audiocraft.models import MusicGen, AudioGen
from audiocraft.data.audio import audio_write
import torch
import os

# Load models
print("Loading MusicGen model...")
music_model = MusicGen.get_pretrained('facebook/musicgen-small')

print("Loading AudioGen model...")
audio_model = AudioGen.get_pretrained('facebook/audiogen-medium')

def generate_music(prompt, duration, top_k, temperature):
    """Generate music from text prompt."""
    music_model.set_generation_params(
        duration=duration,
        top_k=top_k,
        temperature=temperature
    )
    
    descriptions = [prompt]
    wav = music_model.generate(descriptions)
    
    output_path = "/app/outputs/generated_music.wav"
    os.makedirs("/app/outputs", exist_ok=True)
    
    audio_write(
        output_path.replace('.wav', ''),
        wav[0].cpu(),
        music_model.sample_rate,
        format="wav"
    )
    
    return output_path

def generate_sound(prompt, duration, top_k, temperature):
    """Generate sound effect from text prompt."""
    audio_model.set_generation_params(
        duration=duration,
        top_k=top_k,
        temperature=temperature
    )
    
    descriptions = [prompt]
    wav = audio_model.generate(descriptions)
    
    output_path = "/app/outputs/generated_sound.wav"
    os.makedirs("/app/outputs", exist_ok=True)
    
    audio_write(
        output_path.replace('.wav', ''),
        wav[0].cpu(),
        audio_model.sample_rate,
        format="wav"
    )
    
    return output_path

# Create Gradio interface
with gr.Blocks(title="AudioCraft") as demo:
    gr.Markdown("# 🎵 AudioCraft - AI Music & Sound Generation")
    gr.Markdown("Generate music and sound effects from text descriptions using Meta's AudioCraft models.")
    
    with gr.Tab("MusicGen"):
        with gr.Row():
            with gr.Column():
                music_prompt = gr.Textbox(
                    label="Describe the music",
                    placeholder="A calm piano melody in a cozy coffee shop...",
                    lines=3
                )
                music_duration = gr.Slider(1, 30, value=10, step=1, label="Duration (seconds)")
                music_topk = gr.Slider(1, 250, value=250, step=1, label="Top-K")
                music_temp = gr.Slider(0.1, 2.0, value=1.0, step=0.1, label="Temperature")
                music_btn = gr.Button("Generate Music", variant="primary")
            
            with gr.Column():
                music_output = gr.Audio(label="Generated Music", type="filepath")
        
        music_btn.click(
            fn=generate_music,
            inputs=[music_prompt, music_duration, music_topk, music_temp],
            outputs=music_output
        )
    
    with gr.Tab("AudioGen"):
        with gr.Row():
            with gr.Column():
                sound_prompt = gr.Textbox(
                    label="Describe the sound",
                    placeholder="A dog barking in the distance...",
                    lines=3
                )
                sound_duration = gr.Slider(1, 10, value=5, step=1, label="Duration (seconds)")
                sound_topk = gr.Slider(1, 250, value=250, step=1, label="Top-K")
                sound_temp = gr.Slider(0.1, 2.0, value=1.0, step=0.1, label="Temperature")
                sound_btn = gr.Button("Generate Sound", variant="primary")
            
            with gr.Column():
                sound_output = gr.Audio(label="Generated Sound", type="filepath")
        
        sound_btn.click(
            fn=generate_sound,
            inputs=[sound_prompt, sound_duration, sound_topk, sound_temp],
            outputs=sound_output
        )
    
    gr.Markdown("---")
    gr.Markdown("Powered by [Meta's AudioCraft](https://github.com/facebookresearch/audiocraft)")

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
EOF

# Create outputs directory
RUN mkdir -p /app/outputs

# Create non-root user
RUN useradd -m -s /bin/bash appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=15s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/')" || exit 1

CMD ["python", "/app/app.py"]
