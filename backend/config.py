"""
Configuration module for voicebox backend.

Handles data directory configuration for production bundling.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
_DATA_SUBDIRECTORIES = frozenset({"cache", "captures", "generations", "models", "profiles"})

# Allow users to override the HuggingFace model download directory.
# Set VOICEBOX_MODELS_DIR to an absolute path before starting the server.
# This sets HF_HUB_CACHE so all huggingface_hub downloads go to that path.
_custom_models_dir = os.environ.get("VOICEBOX_MODELS_DIR")
if _custom_models_dir:
    os.environ["HF_HUB_CACHE"] = _custom_models_dir
    logger.info("Model download path set to: %s", _custom_models_dir)

# Default data directory (used in development)
_data_dir = Path("data").resolve()


def _path_relative_to_any_data_dir(path: Path) -> Path | None:
    """Extract a recognizable legacy data path from an absolute path."""
    parts = path.parts
    for idx, part in enumerate(parts):
        if part != "data":
            continue

        tail = parts[idx + 1 :]
        for subdirectory_idx, subdirectory in enumerate(tail):
            if (
                subdirectory in _DATA_SUBDIRECTORIES
                and len(tail) - subdirectory_idx >= 2
            ):
                return Path(*tail[subdirectory_idx:])

    return None


def set_data_dir(path: str | Path):
    """
    Set the data directory path.

    Args:
        path: Path to the data directory
    """
    global _data_dir
    _data_dir = Path(path).resolve()
    _data_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Data directory set to: %s", _data_dir)


def get_data_dir() -> Path:
    """
    Get the data directory path.

    Returns:
        Path to the data directory
    """
    return _data_dir


def to_storage_path(path: str | Path) -> str:
    """Convert a filesystem path to a DB-safe path relative to the data dir."""
    resolved_path = Path(path).resolve()

    try:
        return str(resolved_path.relative_to(_data_dir.resolve()))
    except ValueError:
        # Preserve external absolute references exactly. Their interpretation
        # must not depend on which ancestor happens to be named "data".
        return str(resolved_path)


def resolve_storage_path(path: str | Path | None) -> Path | None:
    """Resolve a DB-stored path against the configured data dir."""
    if path is None:
        return None

    stored_path = Path(path)
    # Empty paths (e.g. failed generations) must not resolve to the data
    # dir itself, which exists and would defeat the callers' 404 guards.
    # Path("") is truthy, so check parts rather than the raw value.
    if not stored_path.parts:
        return None
    if stored_path.is_absolute():
        resolved_path = stored_path.resolve()
        data_dir = _data_dir.resolve()
        try:
            resolved_path.relative_to(data_dir)
            return resolved_path
        except ValueError:
            pass

        # Keep existing external absolute references exact. Legacy rebasing is
        # only a fallback for an absolute path that no longer exists.
        if resolved_path.exists():
            return resolved_path

        rebased_path = _path_relative_to_any_data_dir(resolved_path)
        if rebased_path is not None:
            candidate = (data_dir / rebased_path).resolve()
            try:
                candidate.relative_to(data_dir)
            except ValueError:
                return None
            return candidate

        return resolved_path

    # 0.3.0 records sometimes stored relative paths with the data-dir name
    # baked in (e.g. "data/profiles/..."). Joining those directly with
    # _data_dir produces a spurious "<data_dir>/data/profiles/..." nest.
    data_dir = _data_dir.resolve()
    candidate = (data_dir / stored_path).resolve()
    try:
        candidate.relative_to(data_dir)
    except ValueError:
        return None

    if (
        len(stored_path.parts) > 1
        and stored_path.parts[0] == "data"
        and stored_path.parts[1] in _DATA_SUBDIRECTORIES
    ):
        legacy_tail = Path(*stored_path.parts[1:]) if len(stored_path.parts) > 1 else Path()
        legacy_candidate = (data_dir / legacy_tail).resolve()
        try:
            legacy_candidate.relative_to(data_dir)
        except ValueError:
            return None

        if candidate.exists() and legacy_candidate.exists() and candidate != legacy_candidate:
            logger.warning("Ambiguous legacy storage path %s under %s", stored_path, data_dir)
            return None
        if legacy_candidate.exists() or not candidate.exists():
            return legacy_candidate

    return candidate


def get_db_path() -> Path:
    """Get database file path."""
    return _data_dir / "voicebox.db"


def get_profiles_dir() -> Path:
    """Get profiles directory path."""
    path = _data_dir / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_generations_dir() -> Path:
    """Get generations directory path."""
    path = _data_dir / "generations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_captures_dir() -> Path:
    """Get captures directory path."""
    path = _data_dir / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_dir() -> Path:
    """Get cache directory path."""
    path = _data_dir / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_models_dir() -> Path:
    """Get models directory path."""
    path = _data_dir / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Voicebox Cloud (backup & sync). Two hosts: the web app owns auth + device
# pairing (voicebox.sh), the API owns sync + account endpoints
# (api.voicebox.sh). Override both for local development, e.g.
# VOICEBOX_CLOUD_URL=http://localhost:17592 VOICEBOX_CLOUD_API_URL=http://localhost:17593
def get_cloud_web_url() -> str:
    """Base URL of the Voicebox Cloud web app (auth + /connect + exchange)."""
    return os.environ.get("VOICEBOX_CLOUD_URL", "https://voicebox.sh").rstrip("/")


def get_cloud_api_url() -> str:
    """Base URL of the Voicebox Cloud API (bearer-authenticated sync/account)."""
    return os.environ.get("VOICEBOX_CLOUD_API_URL", "https://api.voicebox.sh").rstrip("/")
