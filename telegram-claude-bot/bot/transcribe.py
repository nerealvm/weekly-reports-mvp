"""Расшифровка голосовых и аудио локальным faster-whisper."""

import asyncio

from . import config

_model = None
_lock = asyncio.Lock()


def _load_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def _transcribe_sync(path: str) -> str:
    model = _load_model()
    segments, _info = model.transcribe(path, vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()


async def transcribe(path: str) -> str:
    # Одна расшифровка за раз: whisper на CPU, параллелить нет смысла.
    async with _lock:
        return await asyncio.to_thread(_transcribe_sync, path)
