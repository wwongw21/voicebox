"""Read-only reproductions for voicebox HEAD 51f49dea.

Run: PYTHONPATH=/workspace/repos/voicebox python /workspace/review/voicebox_repro.py
Only temporary files are written; no model weights or audio are needed.
"""

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from backend import config
from backend.models import EffectConfig, GenerationRequest, VoiceProfileResponse
from backend.services import task_queue


def storage_round_trip():
    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data" / "voicebox"
        config.set_data_dir(data_dir)
        source = config.get_generations_dir() / "example.wav"
        source.write_bytes(b"audio")
        stored = config.to_storage_path(source)
        actual = config.resolve_storage_path(stored)
        assert actual != source
        print("Storage path bug reproduced:", stored, "resolves to", actual)


async def cancellation_overlap():
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = asyncio.Event()
    overlap = []

    def first_inference():
        first_started.set()
        release_first.wait(timeout=5)

    async def first_job():
        try:
            await asyncio.to_thread(first_inference)
        except asyncio.CancelledError:
            pass  # run_generation records failure and also consumes cancellation

    async def second_job():
        overlap.append(first_started.is_set() and not release_first.is_set())
        second_started.set()

    task_queue.init_queue(force=True)
    task_queue.enqueue_generation("first", first_job())
    while not first_started.is_set():
        await asyncio.sleep(0.01)
    task_queue.enqueue_generation("second", second_job())
    assert task_queue.cancel_generation("first") == "running"
    try:
        await asyncio.wait_for(second_started.wait(), timeout=1)
        assert overlap == [True]
        print("Cancellation bug reproduced: next job started during cancelled thread's inference")
    finally:
        release_first.set()
        await task_queue._generation_queue.join()
        task_queue._generation_worker_task.cancel()
        try:
            await task_queue._generation_worker_task
        except asyncio.CancelledError:
            pass


def request_and_effect_contracts():
    request = GenerationRequest(profile_id="preset-kokoro", text="Hello")
    resolved_engine = request.engine or "kokoro"  # route resolver with kokoro profile
    assert resolved_engine == "qwen"
    print("Engine default bug reproduced: omitted engine overrides profile's kokoro default with qwen")
    profile = VoiceProfileResponse(
        id="p", name="Voice", description=None, language="en",
        effects_chain=[EffectConfig(type="reverb", enabled=True, params={})],
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    try:
        json.loads(profile.effects_chain)  # stream_speech's current fallback
    except TypeError:
        print("Stream effect bug reproduced: profile's deserialized effects list fails json.loads")
    else:
        raise AssertionError("Expected stream_speech parser failure")


if __name__ == "__main__":
    storage_round_trip()
    asyncio.run(cancellation_overlap())
    request_and_effect_contracts()
