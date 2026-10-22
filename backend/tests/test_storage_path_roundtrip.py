import shutil
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend import config
from backend.database import Base, Generation, VoiceProfile, get_db
from backend.database.migrations import _normalize_storage_paths
from backend.routes.audio import router as audio_router


ROOT_LAYOUTS = [
    ("project", "data"),
    ("voicebox-store",),
    ("data", "voicebox"),
    ("data", "data", "voicebox"),
]
IN_ROOT_PATHS = [
    "generations/original.wav",
    "generations/processed.wav",
    "generations/alternate.wav",
    "profiles/profile-1/samples/sample.wav",
    "profiles/profile-1/avatar.png",
    "captures/capture.wav",
]


@pytest.mark.parametrize("root_layout", ROOT_LAYOUTS)
@pytest.mark.parametrize("relative_path", IN_ROOT_PATHS)
def test_configured_root_paths_round_trip(tmp_path, monkeypatch, root_layout, relative_path):
    data_dir = tmp_path.joinpath(*root_layout)
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    source = data_dir / Path(relative_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"audio")

    stored_path = config.to_storage_path(source)

    assert Path(stored_path) == Path(relative_path)
    assert config.resolve_storage_path(stored_path) == source.resolve()


def test_symlinked_configured_root_round_trips_after_resolve(tmp_path, monkeypatch):
    real_root = tmp_path / "real" / "data" / "voicebox"
    real_root.mkdir(parents=True)
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Directory symlinks are unavailable: {error}")

    data_dir = linked_parent / "data" / "voicebox"
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())
    source = data_dir / "generations" / "voice.wav"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"audio")

    stored_path = config.to_storage_path(source)

    assert stored_path == str(Path("generations") / "voice.wav")
    assert config.resolve_storage_path(stored_path) == source.resolve()


def test_external_absolute_path_round_trips_without_data_ancestor_rebasing(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data" / "voicebox"
    external_file = tmp_path / "external" / "data" / "voice.wav"
    data_dir.mkdir(parents=True)
    external_file.parent.mkdir(parents=True)
    external_file.write_bytes(b"external")
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    stored_path = config.to_storage_path(external_file)

    assert Path(stored_path) == external_file.resolve()
    assert config.resolve_storage_path(stored_path) == external_file.resolve()


def test_relative_reference_survives_moving_the_complete_data_root(tmp_path, monkeypatch):
    old_root = tmp_path / "old" / "data" / "voicebox"
    original_file = old_root / "generations" / "generation.wav"
    original_file.parent.mkdir(parents=True)
    original_file.write_bytes(b"moved audio")
    monkeypatch.setattr(config, "_data_dir", old_root.resolve())
    stored_path = config.to_storage_path(original_file)

    new_root = tmp_path / "new" / "data" / "voicebox"
    new_root.parent.mkdir(parents=True)
    shutil.copytree(old_root, new_root)
    monkeypatch.setattr(config, "_data_dir", new_root.resolve())

    resolved_path = config.resolve_storage_path(stored_path)

    assert stored_path == str(Path("generations") / "generation.wav")
    assert resolved_path == new_root.joinpath("generations", "generation.wav").resolve()
    assert resolved_path.read_bytes() == b"moved audio"


def test_legacy_relative_data_prefix_remains_readable(tmp_path, monkeypatch):
    data_dir = tmp_path / "voicebox-store"
    legacy_target = data_dir / "profiles" / "profile-1" / "sample.wav"
    legacy_target.parent.mkdir(parents=True)
    legacy_target.write_bytes(b"legacy")
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    assert (
        config.resolve_storage_path("data/profiles/profile-1/sample.wav")
        == legacy_target.resolve()
    )


def test_ambiguous_legacy_relative_path_is_not_rebased_to_another_file(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "voicebox-store"
    normal_target = data_dir / "data" / "profiles" / "sample.wav"
    legacy_target = data_dir / "profiles" / "sample.wav"
    normal_target.parent.mkdir(parents=True)
    legacy_target.parent.mkdir(parents=True)
    normal_target.write_bytes(b"normal")
    legacy_target.write_bytes(b"legacy")
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    assert config.resolve_storage_path("data/profiles/sample.wav") is None


def test_legacy_absolute_path_rebases_only_when_original_is_missing(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "new-root"
    original = tmp_path / "data" / "profiles" / "sample.wav"
    rebased = data_dir / "profiles" / "sample.wav"
    rebased.parent.mkdir(parents=True)
    rebased.write_bytes(b"new root")
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    assert config.resolve_storage_path(original) == rebased.resolve()

    original.parent.mkdir(parents=True)
    original.write_bytes(b"original absolute file")
    assert config.resolve_storage_path(original) == original.resolve()


def test_absolute_path_with_only_a_matching_basename_is_not_rebased(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "voicebox-store"
    original = tmp_path / "external" / "data" / "sample.wav"
    misleading_candidate = data_dir / "sample.wav"
    data_dir.mkdir(parents=True)
    misleading_candidate.write_bytes(b"unrelated")
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    assert config.resolve_storage_path(original) == original.resolve()


def test_absolute_external_path_with_intervening_directory_is_not_rebased(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "voicebox-store"
    original = tmp_path / "archive" / "data" / "shared" / "profiles" / "p" / "avatar.png"
    unrelated = data_dir / "profiles" / "p" / "avatar.png"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"current avatar")
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    assert config.resolve_storage_path(original) == original.resolve()


def test_startup_path_normalization_keeps_ambiguous_external_row_unchanged(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "voicebox-store"
    original = tmp_path / "archive" / "data" / "shared" / "profiles" / "p" / "avatar.png"
    unrelated = data_dir / "profiles" / "p" / "avatar.png"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"current avatar")
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.sqlite').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE profiles (id TEXT PRIMARY KEY, avatar_path TEXT)"))
        connection.execute(
            text("INSERT INTO profiles (id, avatar_path) VALUES ('profile-1', :path)"),
            {"path": str(original)},
        )

    _normalize_storage_paths(engine, {"profiles"})

    with engine.connect() as connection:
        stored_path = connection.execute(
            text("SELECT avatar_path FROM profiles WHERE id='profile-1'")
        ).scalar_one()
    engine.dispose()

    assert stored_path == str(original)


def test_startup_path_normalization_does_not_guess_through_intervening_directories(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "voicebox-store"
    original = tmp_path / "archive" / "data" / "shared" / "profiles" / "p" / "avatar.png"
    unrelated = data_dir / "profiles" / "p" / "avatar.png"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unrelated current avatar")
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    engine = create_engine(f"sqlite:///{(tmp_path / 'ambiguous.sqlite').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE profiles (id TEXT PRIMARY KEY, avatar_path TEXT)"))
        connection.execute(
            text("INSERT INTO profiles (id, avatar_path) VALUES ('profile-1', :path)"),
            {"path": str(original)},
        )

    _normalize_storage_paths(engine, {"profiles"})

    with engine.connect() as connection:
        stored_path = connection.execute(
            text("SELECT avatar_path FROM profiles WHERE id='profile-1'")
        ).scalar_one()
    engine.dispose()

    assert stored_path == str(original)


def test_legacy_absolute_path_with_standard_data_root_remains_readable(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "new-root" / "data" / "voicebox"
    old_absolute_path = tmp_path / "old-root" / "data" / "generations" / "old.wav"
    current_file = data_dir / "generations" / "old.wav"
    current_file.parent.mkdir(parents=True)
    current_file.write_bytes(b"moved data")
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    assert config.resolve_storage_path(old_absolute_path) == current_file.resolve()


@pytest.mark.parametrize("empty_path", [None, "", Path("")])
def test_empty_storage_path_stays_unresolved(empty_path):
    assert config.resolve_storage_path(empty_path) is None


def test_relative_path_traversal_cannot_escape_configured_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "voicebox-store"
    data_dir.mkdir()
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())

    assert config.resolve_storage_path("../outside.wav") is None


@pytest.fixture
def custom_root_audio_client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data" / "voicebox"
    monkeypatch.setattr(config, "_data_dir", data_dir.resolve())
    audio_file = config.get_generations_dir() / "generation.wav"
    audio_file.write_bytes(b"generated audio")

    database_path = tmp_path / "audio-test.sqlite"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = session_factory()
    db.add(VoiceProfile(id="profile-1", name="Test Profile"))
    stored_path = config.to_storage_path(audio_file)
    db.add(
        Generation(
            id="generation-1",
            profile_id="profile-1",
            text="sample",
            audio_path=stored_path,
            status="completed",
        )
    )
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(audio_router)

    def override_get_db():
        connection = session_factory()
        try:
            yield connection
        finally:
            connection.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, stored_path, audio_file

    engine.dispose()


def test_audio_endpoint_serves_file_written_under_custom_root(custom_root_audio_client):
    client, stored_path, audio_file = custom_root_audio_client

    response = client.get("/audio/generation-1")

    assert stored_path == str(Path("generations") / "generation.wav")
    assert response.status_code == 200
    assert response.content == audio_file.read_bytes()
