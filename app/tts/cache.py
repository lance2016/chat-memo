"""Discover TTS model snapshots already present in the Hugging Face cache."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings


def _default_cache() -> Path:
    return Path.home() / ".cache" / "huggingface" / "hub"


def _is_tts_repo(model_id: str) -> bool:
    """Keep unrelated cached LLM/embedding repos out of the voice model picker."""
    name = model_id.lower()
    return any(token in name for token in ("tts", "kokoro", "chatterbox", "oute", "dia-"))


def _is_asr_repo(model_id: str) -> bool:
    """Keep only speech-recognition families in the ASR model picker."""
    name = model_id.lower()
    return any(
        token in name
        for token in ("asr", "whisper", "parakeet", "voxtral", "sensevoice", "funasr")
    )


def _list_cached_models(
    settings: Settings, predicate
) -> list[dict[str, int | str]]:
    root = Path(settings.tts_model_cache).expanduser() if settings.tts_model_cache else _default_cache()
    if not root.is_dir():
        return []

    result: list[dict[str, int | str]] = []
    for repo in sorted(root.glob("models--*")):
        encoded = repo.name.removeprefix("models--")
        if "--" not in encoded:
            continue
        model_id = encoded.replace("--", "/", 1)
        snapshots = repo / "snapshots"
        if not predicate(model_id) or not snapshots.is_dir() or not any(snapshots.iterdir()):
            continue
        size = sum(
            item.stat().st_size
            for item in (repo / "blobs").glob("*")
            if item.is_file()
        )
        result.append({"id": model_id, "size_bytes": size})
    return result


def list_cached_models(settings: Settings) -> list[dict[str, int | str]]:
    """List complete-looking cached TTS repos and their on-disk blob sizes."""
    return _list_cached_models(settings, _is_tts_repo)


def list_cached_asr_models(settings: Settings) -> list[dict[str, int | str]]:
    """List complete-looking cached speech-recognition model repositories."""
    return _list_cached_models(settings, _is_asr_repo)
