from faster_whisper import WhisperModel
import os, tempfile
from dotenv import load_dotenv

load_dotenv()

_model = None

def get_model():
    global _model
    if _model is None:
        _model = WhisperModel(
            os.getenv("WHISPER_MODEL", "large-v3"),
            device=os.getenv("WHISPER_DEVICE", "cuda"),
            compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "float16")
        )
    return _model

def transcribe_audio(audio_bytes: bytes) -> str:
    model = get_model()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        segments, _ = model.transcribe(tmp_path, language="en", vad_filter=True)
        return " ".join(s.text.strip() for s in segments)
    finally:
        os.unlink(tmp_path)
