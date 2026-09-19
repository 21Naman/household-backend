from pathlib import Path
from tempfile import NamedTemporaryFile


def _transcript_from(model, path: Path) -> dict[str, str]:
    segments, info = model.transcribe(str(path), vad_filter=True)
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    if not transcript:
        raise ValueError("No speech could be read from this audio")
    return {"transcript": transcript, "language": getattr(info, "language", "unknown")}


def _decode_to_pcm_wav(source: Path) -> Path:
    """Recover decodable AAC streams that contain an isolated malformed packet."""
    try:
        import av
    except ImportError as exc:  # installed with faster-whisper
        raise RuntimeError("Local audio decoder is unavailable") from exc

    with NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        destination = Path(handle.name)
    frames_written = 0
    try:
        import wave

        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            with av.open(source) as container:
                stream = next(item for item in container.streams if item.type == "audio")
                for packet in container.demux(stream):
                    try:
                        decoded = packet.decode()
                    except av.error.InvalidDataError:
                        continue
                    for frame in decoded:
                        for pcm in resampler.resample(frame):
                            output.writeframes(bytes(pcm.planes[0]))
                            frames_written += pcm.samples
                for pcm in resampler.resample(None):
                    output.writeframes(bytes(pcm.planes[0]))
                    frames_written += pcm.samples
        if not frames_written:
            raise ValueError("No decodable audio frames")
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise


# Ticket #25's cross-check: a two-letter Whisper language code (e.g. "en")
# against a household/cook-profile language name (e.g. "Tamil"). Not exact
# science -- this is a data-quality signal, not a hard gate, per Ticket #25's
# spec ("flag mismatches ... rather than silently trusting it").
_LANGUAGE_NAME_TO_CODE = {
    "english": "en", "hindi": "hi", "tamil": "ta", "telugu": "te",
    "bengali": "bn", "marathi": "mr", "gujarati": "gu", "kannada": "kn",
    "malayalam": "ml", "punjabi": "pa", "urdu": "ur",
}


def language_mismatch(detected_code: str, expected_language_name: str) -> bool:
    """Returns True when Whisper's auto-detected language code disagrees
    with the household/cook's stored language. Unknown expected languages
    never raise a mismatch (nothing to compare against)."""
    expected_code = _LANGUAGE_NAME_TO_CODE.get(expected_language_name.strip().lower())
    if expected_code is None or not detected_code or detected_code == "unknown":
        return False
    return detected_code.lower() != expected_code


class WhisperProvider:
    """Optional, local faster-whisper adapter. Audio is never sent off-device."""

    def __init__(self, model_name: str = "base", device: str = "cpu", compute_type: str = "int8") -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type

    def transcribe(self, audio: bytes, suffix: str = ".webm") -> dict[str, str]:
        if not audio:
            raise ValueError("The audio file is empty or corrupt")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is unavailable; use typed inventory entry") from exc
        path: Path | None = None
        normalized_path: Path | None = None
        try:
            with NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(audio)
                path = Path(handle.name)
            # Model names otherwise trigger faster-whisper's Hugging Face Hub
            # `snapshot_download` path. This application is local-first: use
            # only a previously cached model and fall back to typed entry when
            # it is absent.
            model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True,
            )
            try:
                return _transcript_from(model, path)
            except Exception:
                normalized_path = _decode_to_pcm_wav(path)
                return _transcript_from(model, normalized_path)
        except Exception as exc:
            raise ValueError("The audio file could not be transcribed") from exc
        finally:
            if path:
                path.unlink(missing_ok=True)
            if normalized_path:
                normalized_path.unlink(missing_ok=True)
