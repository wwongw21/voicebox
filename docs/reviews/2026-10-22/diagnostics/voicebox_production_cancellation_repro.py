"""Exercise unchanged production cancellation functions with a fake blocking model.

AST extraction avoids installing Torch/model weights. Executed function bodies:
run_generation -> generate_chunked -> PyTorchTTSBackend.generate -> to_thread.
Only model execution, database I/O, backend lookup and audio I/O are test doubles.
"""

import __future__
import ast
import asyncio
import sys
import threading
import traceback
import types
from pathlib import Path

import numpy as np

from backend.services import task_queue
from backend.utils.tasks import get_task_manager

ROOT = Path('/workspace/repos/voicebox')


def extracted_function(relative_path, name, class_name=None, globals_dict=None):
    parsed = ast.parse((ROOT / relative_path).read_text())
    parent = parsed
    if class_name:
        parent = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    function = next(node for node in parent.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    source_module = ast.Module(body=[function], type_ignores=[])
    namespace = dict(globals_dict or {})
    exec(compile(source_module, str(ROOT / relative_path), 'exec', flags=__future__.annotations.compiler_flag), namespace)
    return namespace[name]


async def main():
    started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    statuses = []
    closed = []
    overlap = []

    class FakeModel:
        def generate_voice_clone(self, *, text, **kwargs):
            if text == 'first':
                started.set()
                release.wait(timeout=10)
            else:
                overlap.append(started.is_set() and not release.is_set())
                second_started.set()
            return [np.zeros(10, dtype=np.float32)], 24000

    async def loaded(*args):
        return None

    generate = extracted_function(
        'backend/backends/pytorch_backend.py', 'generate', 'PyTorchTTSBackend',
        {'asyncio': asyncio, 'LANGUAGE_CODE_TO_NAME': {'en': 'English'}},
    )
    backend = types.SimpleNamespace(model=FakeModel(), load_model_async=loaded, is_loaded=lambda: True)
    backend.generate = types.MethodType(generate, backend)

    backends_module = types.ModuleType('backend.backends')
    backends_module.engine_needs_trim = lambda engine: False
    backends_module.engine_retries_runaway = lambda engine: False
    backends_module.get_tts_backend_for_engine = lambda engine: backend
    backends_module.load_engine_model = loaded
    sys.modules['backend.backends'] = backends_module

    audio_module = types.ModuleType('backend.utils.audio')
    for name in ['has_tts_runaway', 'normalize_audio', 'save_audio', 'trim_tts_output']:
        setattr(audio_module, name, lambda *args: None)
    sys.modules['backend.utils.audio'] = audio_module

    async def update_status(generation_id, status, db, **kwargs):
        statuses.append((generation_id, status))

    async def voice_prompt(*args, **kwargs):
        return {}

    def get_db():
        yield types.SimpleNamespace(close=lambda: closed.append(True))

    run_generation = extracted_function(
        'backend/services/generation.py', 'run_generation', globals_dict={
            '__package__': 'backend.services',
            'asyncio': asyncio,
            'traceback': traceback,
            'history': types.SimpleNamespace(update_generation_status=update_status),
            'profiles': types.SimpleNamespace(create_voice_prompt_for_profile=voice_prompt),
            'get_db': get_db,
            'get_task_manager': get_task_manager,
            '_save_retry': lambda **kwargs: 'not-written.wav',
            '_notify_speak_end': lambda *args, **kwargs: None,
        },
    )

    def job(identifier):
        return run_generation(generation_id=identifier, profile_id='p', text=identifier,
                              language='en', engine='qwen', model_size='1.7B', seed=None, mode='retry')

    task_queue.init_queue(force=True)
    task_queue.enqueue_generation('first', job('first'))
    while not started.is_set():
        await asyncio.sleep(.01)
    task_queue.enqueue_generation('second', job('second'))
    assert task_queue.cancel_generation('first') == 'running'
    try:
        for _ in range(100):
            if second_started.is_set():
                break
            await asyncio.sleep(.01)
        assert second_started.is_set()
        assert ('first', 'failed') in statuses
        assert closed  # actual run_generation finally has already closed first session
        assert overlap == [True]
        print('PASS: unchanged production run_generation returned, recorded failed, and closed DB before model thread ended')
        print('PASS: actual chunker and PyTorch generate method started next model call while first was blocked')
        print('Statuses before releasing first model:', statuses)
    finally:
        release.set()
        await task_queue._generation_queue.join()
        task_queue._generation_worker_task.cancel()
        try:
            await task_queue._generation_worker_task
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    asyncio.run(main())
