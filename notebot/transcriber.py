from faster_whisper import WhisperModel
import os, tempfile
from dotenv import load_dotenv

load_dotenv()

_model = None

def get_model():
    global _model
    if _model is None:
        device = os.getenv("WHISPER_DEVICE", "cuda")
        compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
        model_name = os.getenv("WHISPER_MODEL", "large-v3")
        try:
            _model = WhisperModel(model_name, device=device, compute_type=compute_type)
        except Exception as e:
            print(f"[whisper] {device} failed ({e}), falling back to cpu/int8")
            _model = WhisperModel(model_name, device="cpu", compute_type="int8")
    return _model

def transcribe_audio(audio_bytes: bytes) -> str:
    model = get_model()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        segments, _ = model.transcribe(tmp_path, language="en", vad_filter=True)
        return " ".join(s.text.strip() for s in segments)
    finally:
        os.unlink(tmp_path)
