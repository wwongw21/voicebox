import json
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Profile cache annotations name torch.Tensor at import time. These route tests
# replace inference with a fake, so provide only that annotation when torch is
# absent instead of downloading a large model runtime.
_torch_stubbed = False
try:
    _torch_available = importlib.util.find_spec("torch") is not None
except (ImportError, ValueError):
    _torch_available = "torch" in sys.modules
if not _torch_available:
    torch_stub = types.ModuleType("torch")
    torch_stub.Tensor = type("Tensor", (), {})
    sys.modules["torch"] = torch_stub
    _torch_stubbed = True

from backend import models
from backend.database import Base, Generation, VoiceProfile, get_db
from backend.routes import generations
from backend.services import history, task_queue
from backend.utils.tasks import get_task_manager

if _torch_stubbed:
    sys.modules.pop("torch", None)


class FakeDatabase:
    def __init__(self, profile_effects):
        self.profile_effects = profile_effects

    def query(self, _model):
        return self

    def filter_by(self, **_filters):
        return self

    def first(self):
        return SimpleNamespace(effects_chain=self.profile_effects)


def _profile(*, default_engine=None, preset_engine=None, effects_chain=None):
    return SimpleNamespace(
        id="profile-1",
        default_engine=default_engine,
        preset_engine=preset_engine,
        voice_type="preset" if preset_engine else "cloned",
        effects_chain=effects_chain,
    )


@pytest.mark.parametrize(
    ("profile", "explicit_engine", "expected"),
    [
        (_profile(default_engine="kokoro"), None, "kokoro"),
        (_profile(preset_engine="qwen_custom_voice"), None, "qwen_custom_voice"),
        (_profile(default_engine="luxtts"), "chatterbox", "chatterbox"),
        (_profile(), None, "qwen"),
    ],
)
def test_generation_engine_uses_request_then_profile_then_qwen_fallback(
    profile, explicit_engine, expected
):
    request = models.GenerationRequest(
        profile_id="profile-1",
        text="Hello",
        engine=explicit_engine,
    )

    assert request.engine == explicit_engine
    assert generations._resolve_generation_engine(request, profile) == expected


def test_generation_request_leaves_engine_and_model_size_unset_by_default():
    request = models.GenerationRequest(profile_id="profile-1", text="Hello")

    assert request.engine is None
    assert request.model_size is None


def test_invalid_engine_string_is_rejected_by_request_schema():
    with pytest.raises(ValueError):
        models.GenerationRequest(profile_id="profile-1", text="Hello", engine="invalid")


def test_explicit_engine_cannot_override_preset_compatibility():
    profile = SimpleNamespace(
        id="preset-profile",
        voice_type="preset",
        preset_engine="kokoro",
        preset_voice_id="af_bella",
    )

    with pytest.raises(ValueError, match="only supports engine 'kokoro'"):
        generations.profiles.validate_profile_engine(profile, "qwen")


@pytest.mark.parametrize(
    ("engine", "requested_size", "expected_size"),
    [
        ("qwen", None, "1.7B"),
        ("qwen_custom_voice", "0.6B", "0.6B"),
        ("tada", None, "1B"),
        ("tada", "3B", "3B"),
        ("kokoro", "1.7B", None),
    ],
)
def test_model_size_default_matches_the_resolved_engine(engine, requested_size, expected_size):
    request = models.GenerationRequest(
        profile_id="profile-1",
        text="Hello",
        model_size=requested_size,
    )

    assert generations._resolve_generation_model_size(request, engine) == expected_size


def test_model_size_rejects_variant_incompatible_with_engine():
    request = models.GenerationRequest(
        profile_id="profile-1",
        text="Hello",
        model_size="1.7B",
    )

    with pytest.raises(HTTPException) as error:
        generations._resolve_generation_model_size(request, "tada")

    assert error.value.status_code == 400


def _install_persisted_route_fakes(monkeypatch, profile, raw_effects, captured):
    async def get_profile(_profile_id, _db):
        return profile

    def validate_profile_engine(_profile, _engine):
        return None

    async def create_generation(**kwargs):
        captured["generation"] = kwargs
        return SimpleNamespace(id=kwargs["generation_id"])

    async def no_op_generation():
        return None

    def make_generation(**kwargs):
        captured["run_generation"] = kwargs
        return no_op_generation()

    task_manager = SimpleNamespace(start_generation=lambda **_kwargs: None)

    monkeypatch.setattr(generations.profiles, "get_profile", get_profile)
    monkeypatch.setattr(generations.profiles, "validate_profile_engine", validate_profile_engine)
    monkeypatch.setattr(generations.history, "create_generation", create_generation)
    monkeypatch.setattr(generations, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(generations, "run_generation", make_generation)
    monkeypatch.setattr(generations, "enqueue_generation", lambda _id, coro: coro.close())

    return FakeDatabase(json.dumps(raw_effects) if raw_effects is not None else None)


def _install_stream_route_fakes(monkeypatch, profile, captured, *, effects=None):
    import backend.backends as backends
    from backend.utils import chunked_tts
    from backend.utils import audio as audio_utils

    backend = object()

    async def get_profile(_profile_id, _db):
        return profile

    def validate_profile_engine(_profile, _engine):
        return None

    async def no_op(*_args, **_kwargs):
        return None

    def get_backend(engine):
        captured["backend_engine"] = engine
        return backend

    async def create_prompt(_profile_id, _db, **kwargs):
        captured["prompt_engine"] = kwargs["engine"]
        return {"prompt": True}

    async def generate_chunked(_backend, _text, _prompt, **_kwargs):
        return np.array([1.0], dtype=np.float32), 24000

    def encode_wav(audio, _sample_rate):
        captured["encoded_audio"] = np.array(audio, copy=True)
        return np.asarray(audio, dtype=np.float32).tobytes()

    monkeypatch.setattr(generations.profiles, "get_profile", get_profile)
    monkeypatch.setattr(generations.profiles, "validate_profile_engine", validate_profile_engine)
    monkeypatch.setattr(generations.profiles, "create_voice_prompt_for_profile", create_prompt)
    monkeypatch.setattr(backends, "engine_needs_trim", lambda _engine: False)
    monkeypatch.setattr(backends, "engine_retries_runaway", lambda _engine: False)
    monkeypatch.setattr(backends, "ensure_model_cached_or_raise", no_op)
    monkeypatch.setattr(backends, "get_tts_backend_for_engine", get_backend)
    monkeypatch.setattr(backends, "load_engine_model", no_op)
    monkeypatch.setattr(chunked_tts, "generate_chunked", generate_chunked)
    monkeypatch.setattr(generations.tts, "audio_to_wav_bytes", encode_wav)

    if effects is not None:
        effects_module = types.ModuleType("backend.utils.effects")
        effects_module.apply_effects = effects
        monkeypatch.setitem(sys.modules, "backend.utils.effects", effects_module)

    return audio_utils


def _post_stream(data):
    app = FastAPI()
    app.include_router(generations.router)

    def override_get_db():
        yield FakeDatabase(None)

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        return client.post(
            "/generate/stream",
            json=data.model_dump(mode="json", exclude_none=True),
        )


@pytest.mark.asyncio
async def test_persisted_and_stream_routes_use_same_profile_engine_and_effects(monkeypatch):
    default_chain = [
        models.EffectConfig(
            type="gain",
            enabled=True,
            params={"gain_db": 3.5},
        ),
        models.EffectConfig(
            type="reverb",
            enabled=False,
            params={"room_size": 0.6},
        ),
    ]
    raw_chain = [effect.model_dump() for effect in default_chain]
    profile = _profile(default_engine="kokoro", effects_chain=default_chain)
    request = models.GenerationRequest(
        profile_id="profile-1",
        text="Hello",
        normalize=False,
    )

    persisted = {}
    db = _install_persisted_route_fakes(monkeypatch, profile, raw_chain, persisted)
    await generations.generate_speech(request, db)

    effects_seen = []
    processing_order = []

    def apply_effects(audio, _sample_rate, chain):
        effects_seen.append(chain)
        processing_order.append("effects")
        return audio + 1.0

    streamed = {}
    audio_utils = _install_stream_route_fakes(
        monkeypatch,
        profile,
        streamed,
        effects=apply_effects,
    )

    def normalize(audio, *_args, **_kwargs):
        processing_order.append("normalize")
        return audio * 2.0

    # Keep the normalizer observable while making its output deterministic.
    monkeypatch.setattr(audio_utils, "normalize_audio", normalize)
    stream_request = request.model_copy(update={"normalize": True})
    response = _post_stream(stream_request)

    assert persisted["generation"]["engine"] == "kokoro"
    assert persisted["run_generation"]["engine"] == "kokoro"
    assert streamed["backend_engine"] == "kokoro"
    assert streamed["prompt_engine"] == "kokoro"
    assert persisted["run_generation"]["effects_chain"] == raw_chain
    assert effects_seen == [raw_chain]
    assert processing_order == ["effects", "normalize"]
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert streamed["encoded_audio"].tolist() == [4.0]
    assert response.content == np.array([4.0], dtype=np.float32).tobytes()


@pytest.mark.asyncio
async def test_stream_request_chain_overrides_profile_and_empty_chain_disables_effects(monkeypatch):
    profile_chain = [models.EffectConfig(type="gain", params={"gain_db": 2.0})]
    request_chain = [models.EffectConfig(type="gain", params={"gain_db": -4.0})]
    profile = _profile(default_engine="kokoro", effects_chain=profile_chain)

    calls = []

    def apply_effects(audio, _sample_rate, chain):
        calls.append(chain)
        return audio + 1

    _install_stream_route_fakes(monkeypatch, profile, {}, effects=apply_effects)

    explicit = models.GenerationRequest(
        profile_id="profile-1",
        text="Hello",
        effects_chain=request_chain,
        normalize=False,
    )
    response = _post_stream(explicit)
    assert response.status_code == 200
    assert calls == [[request_chain[0].model_dump()]]

    calls.clear()
    disabled = explicit.model_copy(update={"effects_chain": []})
    response = _post_stream(disabled)
    assert response.status_code == 200
    assert calls == []


@pytest.mark.asyncio
async def test_stream_without_profile_effects_returns_audio_without_effect_processing(monkeypatch):
    profile = _profile(default_engine="kokoro", effects_chain=None)
    captured = {}
    _install_stream_route_fakes(monkeypatch, profile, captured, effects=None)
    request = models.GenerationRequest(
        profile_id="profile-1",
        text="Hello",
        normalize=False,
    )

    response = _post_stream(request)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content == np.array([1.0], dtype=np.float32).tobytes()


@pytest.mark.asyncio
async def test_both_generation_routes_choose_compatible_preset_model_size(monkeypatch):
    profile = _profile(preset_engine="qwen_custom_voice")
    request = models.GenerationRequest(profile_id="profile-1", text="Hello", normalize=False)
    persisted = {}
    db = _install_persisted_route_fakes(monkeypatch, profile, None, persisted)
    await generations.generate_speech(request, db)

    loaded = {}
    import backend.backends as backends

    _install_stream_route_fakes(monkeypatch, profile, loaded)

    async def cache_model(engine, size):
        loaded["cache"] = (engine, size)

    async def load_model(engine, size):
        loaded["load"] = (engine, size)

    def get_backend(engine):
        loaded["engine"] = engine
        return object()

    monkeypatch.setattr(backends, "ensure_model_cached_or_raise", cache_model)
    monkeypatch.setattr(backends, "get_tts_backend_for_engine", get_backend)
    monkeypatch.setattr(backends, "load_engine_model", load_model)

    response = _post_stream(request)
    assert response.status_code == 200

    assert persisted["generation"]["engine"] == "qwen_custom_voice"
    assert persisted["generation"]["model_size"] == "1.7B"
    assert persisted["run_generation"]["engine"] == "qwen_custom_voice"
    assert persisted["run_generation"]["model_size"] == "1.7B"
    assert loaded["engine"] == "qwen_custom_voice"
    assert loaded["cache"] == ("qwen_custom_voice", "1.7B")
    assert loaded["load"] == ("qwen_custom_voice", "1.7B")


@pytest.mark.parametrize("invalid_effects_json", ["[1]", '{"type":"gain"}'])
@pytest.mark.asyncio
async def test_invalid_legacy_effects_do_not_orphan_a_persisted_generation(
    tmp_path, monkeypatch, invalid_effects_json
):
    engine = create_engine(f"sqlite:///{(tmp_path / 'generation.sqlite').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    db.add(
        VoiceProfile(
            id="profile-invalid-effects",
            name="Invalid Effects",
            voice_type="cloned",
            effects_chain=invalid_effects_json,
        )
    )
    db.commit()

    task_manager = get_task_manager()
    task_queue.init_queue(force=True)

    async def fake_run_generation(**kwargs):
        background_db = session_factory()
        try:
            await history.update_generation_status(
                generation_id=kwargs["generation_id"],
                status="failed",
                db=background_db,
                error="fake inference failed",
            )
        finally:
            background_db.close()
            task_manager.complete_generation(kwargs["generation_id"])

    monkeypatch.setattr(generations, "run_generation", fake_run_generation)

    try:
        response = await generations.generate_speech(
            models.GenerationRequest(
                profile_id="profile-invalid-effects",
                text="Hello",
            ),
            db,
        )

        await task_queue._generation_queue.join()

        db.expire_all()
        saved = db.query(Generation).filter_by(id=response.id).first()
        assert saved is not None
        assert saved.status == "failed"
        assert saved.error == "fake inference failed"
        assert not task_manager.is_generation_active(response.id)
    finally:
        db.close()
        await task_queue.shutdown_queue()
        engine.dispose()
