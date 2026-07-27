from dataclasses import dataclass
from pathlib import Path
import threading

from faster_whisper import WhisperModel

from app.config import settings


class SpeechServiceError(RuntimeError):
    """Raised when an audio recording cannot become a usable transcript."""


@dataclass(frozen=True)
class SpeechTranslation:
    text: str
    detected_language: str
    language_confidence: float


_model: WhisperModel | None = None
_model_initialization_lock = threading.Lock()
_transcription_lock = threading.Lock()


def get_whisper_model() -> WhisperModel:
    """Create the model once per API process; model construction is expensive."""
    global _model
    if _model is None:
        with _model_initialization_lock:
            if _model is None:
                _model = WhisperModel(
                    settings.WHISPER_MODEL,
                    device=settings.WHISPER_DEVICE,
                    compute_type=settings.WHISPER_COMPUTE_TYPE,
                )
    return _model


def translate_audio(audio_path: Path, language: str | None = None) -> SpeechTranslation:
    """Auto-detect speech and translate it to English without retaining the audio."""
    try:
        with _transcription_lock:
            segments, info = get_whisper_model().transcribe(
                str(audio_path),
                language=language or None,
                task="translate",
                beam_size=5,
                vad_filter=True,
            )
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    except Exception as exc:
        raise SpeechServiceError("The audio could not be transcribed.") from exc

    duration = float(getattr(info, "duration", 0) or 0)
    if duration > settings.MAX_AUDIO_DURATION_SECONDS:
        raise SpeechServiceError(
            f"The recording is longer than the {settings.MAX_AUDIO_DURATION_SECONDS}-second limit."
        )
    if not text:
        raise SpeechServiceError("No clear speech was detected. Please record again.")
    if len(text) > settings.MAX_TRANSCRIPT_CHARS:
        raise SpeechServiceError("The translated recording is too long. Please use a shorter recording.")

    return SpeechTranslation(
        text=text,
        detected_language=str(getattr(info, "language", "unknown") or "unknown"),
        language_confidence=float(getattr(info, "language_probability", 0.0) or 0.0),
    )
