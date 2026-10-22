# voicebox engineering handoff

Review date: **22 October 2026**. All findings remain **OPEN**. This handoff contains proposed repairs and release checks; no source fixes or new runtime acceptance are claimed.

**Reviewed source:** [`main` at `51f49dea198384b4eb6087b72c17057c6eb1c1cd`](https://github.com/wwongw21/voicebox/tree/51f49dea198384b4eb6087b72c17057c6eb1c1cd).

The full structured handoff is in [report.json](report.json). Historical commands retain their original review-workspace paths; read the prerequisites before running them. Only repository-specific diagnostics listed below are attached.

## Audience and required outcome

### Audience

1. Backend engineer implementing repairs

2. Desktop/API engineer checking contract compatibility

3. Release owner validating model and packaging behavior

### Outcome

null

## Scope and reviewed branches

1. Backend app factory, CORS and lifespan setup, route registration and main/server startup contracts

2. Generation orchestration, task cancellation, PyTorch/MLX inference thread boundaries and streaming route

3. Profile CRUD and validation, ZIP import/export paths, storage path conversion and file serving

4. History/version persistence, story joins and editing interfaces, model cache migration/delete paths

5. Desktop permissions/configuration, frontend API request defaults, package/dependency manifests, Docker bindings and CI/release workflows

## Architecture

### Product

Local voice cloning, speech synthesis, dictation and audio editing application, available through a shared React frontend, a Tauri desktop shell, and a standalone FastAPI server.

### Components

#### Item 1

##### Name

Shared UI and API client

##### Paths

1. app/src/components/Generation

2. app/src/components/History/HistoryTable.tsx

3. app/src/lib/api/client.ts

4. app/src/lib/api/types.ts

5. web

##### Responsibility

Profile selection, synthesis requests, history rendering, cancellation requests, and browser frontend distribution.

#### Item 2

##### Name

Desktop host

##### Paths

1. tauri/src-tauri

2. tauri/src-tauri/capabilities/default.json

3. backend/server.py

##### Responsibility

Native capture/playback and desktop integration; backend sidecar startup and parent-process watchdog. Desktop execution was not tested.

#### Item 3

##### Name

HTTP app and request contracts

##### Paths

1. backend/app.py

2. backend/main.py

3. backend/models.py

4. backend/routes

##### Responsibility

FastAPI app lifecycle, CORS, routing, Pydantic request/response parsing, SSE/audio responses, and mounted MCP interface.

#### Item 4

##### Name

Generation orchestration and queue

##### Paths

1. backend/services/task_queue.py

2. backend/services/generation.py

3. backend/utils/chunked_tts.py

4. backend/utils/tasks.py

##### Responsibility

Serial queued inference, status transitions, cancellation, chunked synthesis, audio persistence and task tracking. Direct streaming and nonpersisted generation also reach the backend outside this queue.

#### Item 5

##### Name

Model adapters

##### Paths

1. backend/backends/__init__.py

2. backend/backends/pytorch_backend.py

3. backend/backends/mlx_backend.py

4. backend/backends/*_backend.py

##### Responsibility

Select/load engines and bridge blocking model APIs into async handlers through asyncio.to_thread. Cancellation ownership must match actual thread lifetime.

#### Item 6

##### Name

Persistence and audio effects

##### Paths

1. backend/config.py

2. backend/database

3. backend/services/profiles.py

4. backend/services/history.py

5. backend/services/versions.py

6. backend/utils/effects.py

7. backend/routes/audio.py

##### Responsibility

SQLite records, configured data root, relative paths, profile effects conversion, version files and audio serving.

#### Item 7

##### Name

Verification and distribution

##### Paths

1. backend/tests

2. backend/requirements.txt

3. package.json

4. bun.lock

5. .github/workflows/ci.yml

6. .github/workflows/release.yml

7. Dockerfile

##### Responsibility

Backend regression scripts, dependency declarations, frontend build gates and desktop packaging. Current CI file only typechecks and builds the frontend.

### Critical flows

1. History active row -> Cancel generation button -> apiClient.cancelGeneration -> POST /generate/{id}/cancel -> task_queue.cancel_generation -> Task.cancel -> run_generation CancelledError cleanup. The model thread continues unless explicitly joined/cooperatively stopped.

2. POST /generate -> Pydantic GenerationRequest -> profile response -> engine resolver/validator -> queue -> run_generation -> generate_chunked -> engine.generate -> asyncio.to_thread -> save version/audio -> generation terminal status.

3. Written audio path -> config.to_storage_path -> SQLite audio_path -> config.resolve_storage_path -> audio route is_file guard -> FileResponse.

4. POST /generate/stream -> get_profile returns parsed EffectConfig list -> generation -> request/profile effects resolution -> apply_effects -> optional normalization -> WAV response.

## Immediate mitigations

### Item 1

#### Finding

VB-1

#### Action

Let active model inference finish; cancel only queued work while this defect remains open. After an active cancellation has already occurred, do not submit/retry more generation merely because its row is failed; wait for actual model activity to finish, or fully stop and restart the backend before resuming.

#### Limits

This is an operational workaround, not a code guarantee. Failed UI status does not prove its thread has stopped. Restarting interrupts other clients and queued work. No new configuration flag or safe immediate thread-stop API was found or used.

### Item 2

#### Finding

VB-2

#### Action

For fresh isolated deployments choose a data root without an earlier ancestor component literally named data, such as /srv/voicebox-store. Before changing any existing deployment location, make a consistent backup of SQLite and the complete data directory and inventory current stored paths.

#### Limits

Changing the root does not repair already-malformed rows. Do not bulk rename/move/delete existing audio or rewrite database paths without a verified mapping and backup. The normal final data directory named data works when no earlier ancestor named data takes precedence.

### Item 3

#### Finding

VB-3

#### Action

API callers can explicitly supply the engine matching the profile, or explicitly send engine: null to activate the current resolver fallback.

#### Limits

Every caller must apply this workaround. Omission still selects qwen until the request default changes. Explicit engine/profile mismatches should continue to be rejected.

### Item 4

#### Finding

VB-4

#### Action

Send the intended effects_chain explicitly in streaming requests, or use the persisted /generate flow when profile default effects are required.

#### Limits

Callers must keep the explicit chain synchronized with profile settings. An empty list intentionally disables effects; null or omission currently reaches the broken default fallback. No inference was performed to compare audio perceptually.

## Implementation sequence

### Item 1

#### Order

0

#### Work

Preserve evidence and establish a clean repair baseline

#### Depends on

None recorded.

#### Steps

1. Start from the pinned SHA or explicitly record any newer baseline and revalidate affected evidence.

2. Create an isolated implementation branch/check-out after fixes are authorized; retain these review artifacts.

3. Prepare a consistent data/SQLite backup before exercising any path migration against real user data. Use disposable profiles, generated audio and model fixtures for tests.

#### Completion gate

Baseline identified, backups planned/tested on disposable data, and regressions describe expected repaired behavior.

### Item 2

#### Order

1

#### Work

Repair actual inference lifetime ownership for VB-1

#### Depends on

1. 0

#### Steps

1. Add cancellation regression using a blocked model thread, not only asyncio.Event waits.

2. Retain thread completion ownership across cancellation in the backend inference boundary; keep the queue/resource lease held until the computation ends.

3. Audit equivalent async-to-thread adapter call sites and direct generation routes for the same ownership contract.

#### Completion gate

No second model call can begin while cancelled first inference still owns the resource; cancellation remains responsive as a request acknowledgement.

### Item 3

#### Order

2

#### Work

Repair path serialization and safe legacy handling for VB-2

#### Depends on

1. 0

#### Parallelism

Can proceed independently of VB-1; use a separate change unit and coordinate any shared verification files.

#### Steps

1. Prioritize the configured data root when serializing current paths.

2. Preserve established empty-path and legacy-read behavior with explicit tests.

3. Decide whether existing malformed rows need a separate reversible recovery tool after inspecting real examples; do not silently infer path ownership.

#### Completion gate

New records round-trip across the data-root matrix and legacy records remain readable without ambiguous remapping.

### Item 4

#### Order

3

#### Work

Repair request defaults and stream effects for VB-3/VB-4

#### Depends on

1. 0

#### Parallelism

Both touch generation request contracts/routes; coordinate as adjacent commits to avoid conflicting API tests.

#### Steps

1. Make omitted engine resolve through profile metadata, retaining explicit engine precedence.

2. Serialize the already-parsed profile EffectConfig list directly for streaming effects.

3. Exercise both persisted and streamed endpoints through mocked backend/model seams.

#### Completion gate

Profile engine/effects defaults are consistent across endpoints, with explicit request overrides unchanged.

### Item 5

#### Order

4

#### Work

Repair verification debt and add lightweight backend gates

#### Depends on

1. 1

2. 2

3. 3

#### Steps

1. Align the progress tracker test fixture with the intentional byte threshold and cover the below-threshold behavior separately.

2. Keep model/GPU-dependent tests outside a lightweight deterministic backend regression lane.

3. After implementation authorization, extend CI with the required backend regression lane and use the actual supported dependency set.

#### Completion gate

New maintained regressions pass; the pre-existing progress test inconsistency is resolved; no passing result is claimed for unrun model tests.

### Item 6

#### Order

5

#### Work

Run release validation and stage rollout

#### Depends on

1. 4

#### Steps

1. Perform the NOT RUN release checks listed below on supported platforms.

2. Stage the corrected backend with disposable data first, then a backed-up user data copy.

3. Monitor cancellation queue depth, resource activity, audio 404s and engine/effects mismatch reports.

#### Completion gate

All relevant release checks pass with documented versions/results; rollback can restore application code and any deliberately changed path records without discarding later unrelated user writes.

## Findings and implementation handoff

### VB-1 High: Cancelling an active generation releases the serial queue while inference continues in a thread

**Status:** OPEN. [Pinned source](https://github.com/wwongw21/voicebox/blob/51f49dea198384b4eb6087b72c17057c6eb1c1cd/backend/services/task_queue.py#L105).

#### Evidence classification

Confirmed

#### Observed mechanism

cancel_generation calls running_task.cancel(). The worker awaits only the asyncio task (49-56), while backend/backends/pytorch_backend.py:243, mlx_backend.py:262 and other engines await asyncio.to_thread(_generate_sync). Cancelling that await does not stop an already-running thread. run_generation catches CancelledError and returns (generation.py:137-144), allowing the queue to launch the next job.

#### Impact and scope

Cancelling a long running generation while another is queued causes overlapping inference on the same shared backend/model, defeating the stated GPU serialization guarantee. Extra GPU memory use, model contention and changed random seeds are credible consequences; actual GPU failure was not tested.

#### Repair direction

Keep exclusive inference ownership until the underlying thread completes, or implement cooperative model cancellation that confirms computation has stopped before advancing the queue. Apply the same ownership guard to unqueued synchronous/stream generation paths.

#### Verification already obtained

Reproduced with the real task_queue module and two jobs: first calls asyncio.to_thread on a blocking threading.Event, then is cancelled; second starts before the event is released. /workspace/review/voicebox_repro.py asserts this overlap. Existing cancellation tests use cancellable asyncio.Event waits and do not exercise threaded inference. Stronger independent challenge: /workspace/review/voicebox_production_cancellation_repro.py executes unchanged AST-extracted production run_generation and PyTorchTTSBackend.generate bodies plus the actual generate_chunked and task_queue modules, substituting a blocking model and DB/audio I/O only. It proves first generation writes failed and closes DB, and second generation completes, before the first model thread is released. Frontend reachability: HistoryTable.tsx:468-469 treats generating/loading_model as active; :624-631 offers Cancel generation button; :104-105 invokes apiClient.cancelGeneration; api/client.ts:267-270 posts the production cancel endpoint. No native GUI click was performed.

#### Validation already performed

Reproduced with the real task_queue module and two jobs: first calls asyncio.to_thread on a blocking threading.Event, then is cancelled; second starts before the event is released. /workspace/review/voicebox_repro.py asserts this overlap. Existing cancellation tests use cancellable asyncio.Event waits and do not exercise threaded inference. Stronger independent challenge: /workspace/review/voicebox_production_cancellation_repro.py executes unchanged AST-extracted production run_generation and PyTorchTTSBackend.generate bodies plus the actual generate_chunked and task_queue modules, substituting a blocking model and DB/audio I/O only. It proves first generation writes failed and closes DB, and second generation completes, before the first model thread is released. Frontend reachability: HistoryTable.tsx:468-469 treats generating/loading_model as active; :624-631 offers Cancel generation button; :104-105 invokes apiClient.cancelGeneration; api/client.ts:267-270 posts the production cancel endpoint. No native GUI click was performed.

#### Affected workflows

1. Active Cancel generation action in desktop/web History

2. Queued retry/regenerate after cancelling an in-flight model call

3. Backend engine adapters that offload model work into threads

#### Change surface

##### Item 1

###### File

backend/services/task_queue.py

###### Lines

1. 39

2. 66

3. 105

4. 110

###### Purpose

Queue ownership, cancellation request and worker progression

##### Item 2

###### File

backend/services/generation.py

###### Lines

1. 51

2. 94

3. 137

4. 158

###### Purpose

Await model operation, terminal cancellation handling and resource/session cleanup

##### Item 3

###### File

backend/backends/pytorch_backend.py

###### Lines

1. 202

2. 243

###### Purpose

Qwen PyTorch model inference runs in asyncio.to_thread

##### Item 4

###### File

backend/backends/mlx_backend.py

###### Lines

1. 169

2. 262

###### Purpose

Equivalent MLX threaded inference boundary

##### Item 5

###### File

backend/backends/chatterbox_backend.py

###### Lines

1. 167

2. 226

###### Purpose

Equivalent Chatterbox inference boundary

##### Item 6

###### File

backend/backends/chatterbox_turbo_backend.py

###### Lines

1. 149

2. 206

###### Purpose

Equivalent Chatterbox Turbo inference boundary

##### Item 7

###### File

backend/backends/kokoro_backend.py

###### Lines

1. 242

2. 293

###### Purpose

Equivalent Kokoro inference boundary

##### Item 8

###### File

backend/backends/luxtts_backend.py

###### Lines

1. 143

2. 184

###### Purpose

Equivalent LuxTTS inference boundary

##### Item 9

###### File

backend/backends/hume_backend.py

###### Lines

1. 287

2. 358

###### Purpose

Equivalent TADA inference boundary

##### Item 10

###### File

backend/backends/qwen_custom_voice_backend.py

###### Lines

1. 162

2. 213

###### Purpose

Equivalent CustomVoice inference boundary

##### Item 11

###### File

backend/routes/generations.py

###### Lines

1. 236

2. 280

3. 318

4. 377

###### Purpose

HTTP cancellation and direct stream generation entry points

##### Item 12

###### File

app/src/components/History/HistoryTable.tsx

###### Lines

1. 104

2. 105

3. 468

4. 469

5. 624

6. 631

###### Purpose

Default UI offers cancellation while loading/generating

##### Item 13

###### File

backend/tests/test_task_queue_cancellation.py

###### Lines

1. 1

2. 57

###### Purpose

Existing tests only exercise cancellable asyncio waits

#### Recommended implementation

Retain and join actual threaded model work when the async request is cancelled. Keep the generation queue/resource owner occupied until computation has terminated. Use cooperative engine stopping only where its completion can be proved; otherwise allow the current call to finish before propagating cancellation.

#### Repair steps

1. Introduce a maintained fake-model regression that blocks inside the synchronous inference function. Queue a second real generation coroutine, cancel the first, and assert the second model call has not started until the blocked first model finishes.

2. At each supported inference adapter boundary, retain a handle to the offloaded task and prevent cancellation of the await from losing ownership of the running thread. Defer CancelledError propagation until the thread operation is joined or a cooperative engine stop has actually completed.

3. Make cancellation cleanup robust against repeated cancel requests and shutdown cancellation. A second Task.cancel must not bypass the join/resource lease.

4. Keep the existing immediate endpoint acknowledgement semantics if desired: Generation cancellation requested can return while cleanup waits. Only terminalize/advance the generation worker after the resource is no longer executing.

5. Do not treat awaiting an already-cancelled asyncio task as a thread join. The worker currently sees completion while the detached thread still runs.

6. Audit threaded model loading/voice-prompt creation and direct stream/nonpersisted generation for resource ownership using the same approach. Limit any broader serialization change to a documented contract rather than silently relying on the queued path to cover unqueued work.

7. Retain existing queued-cancel behavior: skip and close a not-yet-started coroutine, clear task tracking, and leave the worker usable.

#### Invariants

1. Cancelling the Python await must not allow a second call to use a shared model/GPU resource while the first synchronous call still owns it.

2. Every queued job is accounted for exactly once; task_done, tracked generation IDs and task-manager completion remain consistent.

3. Cancelled generations never persist completed audio after they are declared terminally failed.

4. Queue progression, model unload and shutdown must respect actual computation lifetime, not merely asyncio Task.done().

5. Cancellation must not deadlock on exceptions raised by the model or status persistence.

#### Acceptance criteria

1. The blocked-thread test shows no overlap and no second model entry until first computation exits, after one or multiple cancellation requests.

2. The real run_generation cleanup does not record terminal cancellation/release ownership ahead of the selected cancellation contract.

3. Queued cancellation still prevents model execution and later jobs run successfully.

4. Model exception during cancellation and status-write failure leave no indefinitely occupied worker/resource lease.

5. On at least one supported GPU backend, cancelling an active job and immediately queuing another produces no overlapping model calls or unbounded extra GPU memory attributable to overlap.

#### Regression cases

1. Cancel before worker starts; cancel during model load; cancel during reference prompt creation; cancel during short and multi-chunk generation.

2. Cancel twice during a blocked inference; cancel again during cancellation cleanup.

3. First model raises after cancellation; DB terminal-status write raises; shutdown occurs during inference.

4. Queue another job and retry the cancelled row; ensure jobs execute serially and IDs/task counts do not leak.

5. Exercise PyTorch and MLX boundary behavior with deterministic fakes; smoke-test other adapters using the same helper/contract.

6. Confirm direct /generate/stream and nonpersisted speak calls cannot invalidate the promised resource ownership policy.

#### Compatibility risks

1. Without cooperative engine stopping, visible cancellation may remain pending until a long noninterruptible model call returns. This delay is intentional if required to preserve exclusive ownership.

2. New locking must cover the real model resource and avoid acquiring locks in conflicting order during model load/unload.

3. Thread joining during shutdown may extend exit time; process watchdog and desktop sidecar lifecycle require explicit validation.

4. A new cancelling status would affect frontend/MCP consumers and database conventions; preserve existing statuses unless a coordinated contract change is needed.

#### Rollback

##### Code

Revert the inference ownership patch and its dependent UI/status changes together. No schema migration is required for the recommended minimal approach.

##### Operational fallback

Return to the active-cancel avoidance workaround. A code rollback restores the original overlap risk; do not label the defect closed.

##### Data

Use disposable data for cancellation experiments. No production data rewrite is part of this repair.

### VB-2 Medium: A custom data directory below an ancestor named data breaks saved audio paths

**Status:** OPEN. [Pinned source](https://github.com/wwongw21/voicebox/blob/51f49dea198384b4eb6087b72c17057c6eb1c1cd/backend/config.py#L63).

#### Evidence classification

Confirmed

#### Observed mechanism

to_storage_path searches the first path component named data (67-69) before attempting to relativize against the actual configured _data_dir. For _data_dir=/tmp/.../data/voicebox, a newly generated /tmp/.../data/voicebox/generations/example.wav becomes voicebox/generations/example.wav; resolve_storage_path then resolves it to /tmp/.../data/voicebox/voicebox/generations/example.wav.

#### Impact and scope

Valid --data-dir configurations such as /mnt/data/voicebox cause generated audio, samples, captures and avatars to be written successfully but saved under paths that fail on retrieval. Audio endpoints return 404 and profiles cannot reload reference audio.

#### Repair direction

First make paths relative to the configured _data_dir. Limit legacy rebasing by a data-named ancestor to legacy reads/migration, after checking the real configured root.

#### Verification already obtained

Executed a stdlib-only round trip with config.set_data_dir(temp/data/voicebox), wrote a temporary generation file, and verified resolve_storage_path(to_storage_path(file)) != file. Reproduction is in /workspace/review/voicebox_repro.py.

#### Validation already performed

Executed a stdlib-only round trip with config.set_data_dir(temp/data/voicebox), wrote a temporary generation file, and verified resolve_storage_path(to_storage_path(file)) != file. Reproduction is in /workspace/review/voicebox_repro.py.

#### Affected workflows

1. Saving/retrieving generated audio and versions

2. Saving/loading profile samples and avatars

3. Capture audio and custom --data-dir deployments

#### Change surface

##### Item 1

###### File

backend/config.py

###### Lines

1. 25

2. 35

3. 63

4. 105

###### Purpose

Current path serialization, legacy data-directory detection and read resolution

##### Item 2

###### File

backend/services/generation.py

###### Lines

1. 191

2. 233

3. 247

4. 249

###### Purpose

Generated audio and version persistence use to_storage_path

##### Item 3

###### File

backend/services/profiles.py

###### Lines

1. 1

2. 25

###### Purpose

Profile sample/avatar persistence depends on config helpers; inspect individual call sites before editing

##### Item 4

###### File

backend/routes/audio.py

###### Lines

1. 25

2. 83

###### Purpose

Stored-path resolution and is_file/404 guards

##### Item 5

###### File

backend/tests/test_audio_failed_generation.py

###### Lines

1. 1

2. 166

###### Purpose

Existing empty-path/directory response regressions

##### Item 6

###### File

backend/database/migrations.py

###### Lines

1. 1

###### Purpose

Only needed if an explicit reversible legacy-row recovery is approved

#### Recommended implementation

Serialize current files relative to the actual configured root before considering any legacy heuristic. Keep legacy read compatibility separate and bounded; do not rewrite existing paths merely because their text contains data.

#### Repair steps

1. Add a matrix of config round-trip tests for the default root, a custom root with no data component, a root below an earlier data ancestor, and repeated data components.

2. In to_storage_path, prioritize resolved_path.relative_to(_data_dir) for current in-root files. Retain an explicit documented fallback for any intentionally supported external absolute paths.

3. Review resolve_storage_path absolute handling so an existing path under the actual configured root is not mistaken for a legacy path based on an earlier data component.

4. Keep support for known 0.3.0 relative data/profiles/... rows and supported historical absolute data paths using tested read rules. Reject ambiguity rather than silently selecting a different existing audio file.

5. Inventory actual malformed persisted rows before proposing recovery. Where a unique existing target can be proven, plan an idempotent mapping with old/new values recorded and a dry-run mode; preserve all audio files.

6. Run endpoint-level file retrieval tests on a temporary SQLite database after path serialization. Confirm file existence, not just string equality.

#### Invariants

1. For a supported in-root file p, resolve_storage_path(to_storage_path(p)) resolves to p regardless of an ancestor named data.

2. New in-root DB paths are portable relative paths; moving the complete data root preserves references.

3. None and empty paths remain None; failed generations and directory-valued paths continue returning 404.

4. Legacy normalization must not overwrite audio, delete files, escape into an unintended storage root, or select a different file based only on a matching basename.

5. Repairing new writes alone must not be presented as having recovered every existing malformed row.

#### Acceptance criteria

1. All configured-root matrix cases round-trip and their audio endpoints serve the actual newly written file.

2. Existing empty/null and directory-path tests continue passing.

3. Representative known legacy relative and absolute records remain readable after the root moves.

4. Any approved row-recovery operation is dry-runnable, idempotent, fully logged and reversible without modifying audio bytes.

#### Regression cases

1. /srv/voicebox-store, /srv/data/voicebox, /srv/data/data/voicebox, default project/data, and a root with symlinked ancestors after resolve().

2. Generated original/processed/alternate versions, profile sample, avatar and capture rows use the same correct storage helper contract.

3. None, empty string, Path("") and a stored directory remain non-file cases.

4. Legacy data/profiles/id/sample.wav; known previous absolute root; an external absolute file if the existing contract intentionally permits it.

5. Both a legacy rebase candidate and an original absolute file exist: do not silently serve the wrong one.

6. Windows native path semantics and drive changes need platform-specific tests rather than assuming POSIX Path represents Windows paths.

#### Compatibility risks

1. Some persisted records rely on historical rebasing. Deleting all legacy handling would trade the current bug for migration regressions.

2. The current serializer may already have written prefixed paths such as voicebox/generations/.... Correcting future serialization does not automatically fix them.

3. A blanket DB restore after users create new generations would discard newer writes. Prefer targeted recorded value reversal for any migration.

4. Changing interpretation of external absolute paths is a separate policy decision and should be documented, not smuggled into this fix.

#### Rollback

##### Code

Revert serializer/reader changes together with their relevant compatibility tests if necessary; leave audio files untouched.

##### Data

Before any approved migration, retain a consistent SQLite/data backup and a per-row old/new mapping. Roll back only mapped values where no subsequent user edit intervened; avoid a full restore after new writes.

##### Fallback

Run only in a verified unaffected root until a safe repair is available. Existing bad rows remain OPEN unless explicitly recovered.

### VB-3 Medium: Omitting engine overrides the selected voice profile default with qwen

**Status:** OPEN. [Pinned source](https://github.com/wwongw21/voicebox/blob/51f49dea198384b4eb6087b72c17057c6eb1c1cd/backend/models.py#L88).

#### Evidence classification

Confirmed

#### Observed mechanism

GenerationRequest.engine is Optional but defaults to qwen. routes/generations.py:54-55 resolves data.engine or profile.default_engine or profile.preset_engine, so the profile fallbacks are unreachable for an omitted engine. For a Kokoro preset, validate_profile_engine rejects the unintended qwen value (services/profiles.py:124-134). Both /generate and /generate/stream use this resolver.

#### Impact and scope

API clients sending the documented minimal profile_id/text request cannot use Kokoro or Qwen CustomVoice preset profiles without explicitly supplying engine or null. A cloned profile with a different default engine also uses qwen silently, potentially loading the wrong model.

#### Repair direction

Default GenerationRequest.engine to None, retaining qwen as the final fallback in the route resolver. Test requests omitting engine for both preset and cloned profiles with a configured default.

#### Verification already obtained

Instantiated the actual GenerationRequest with no engine and confirmed engine == qwen; applying the route resolver expression to a kokoro default resolves qwen. No inference was needed. Reproduction is in /workspace/review/voicebox_repro.py.

#### Detailed handoff evidence

GenerationRequest.engine is Optional but defaults to qwen. routes/generations.py:52-53 resolves data.engine or profile.default_engine or profile.preset_engine, so the profile fallbacks are unreachable for an omitted engine. For a Kokoro preset, validate_profile_engine rejects the unintended qwen value (services/profiles.py:114-135). Both /generate and /generate/stream use this resolver.

#### Validation already performed

Instantiated the actual GenerationRequest with no engine and confirmed engine == qwen; applying the route resolver expression to a kokoro default resolves qwen. No inference was needed. Reproduction is in /workspace/review/voicebox_repro.py.

#### Affected workflows

1. External API clients omitting engine

2. Kokoro and Qwen CustomVoice preset generation

3. Cloned profiles with an explicit default engine

#### Change surface

##### Item 1

###### File

backend/models.py

###### Lines

1. 79

2. 101

###### Purpose

GenerationRequest engine default and OpenAPI schema

##### Item 2

###### File

backend/routes/generations.py

###### Lines

1. 52

2. 53

3. 65

4. 77

5. 332

6. 345

###### Purpose

Shared engine resolver plus persisted/stream generation validation

##### Item 3

###### File

backend/services/profiles.py

###### Lines

1. 114

2. 135

###### Purpose

Preserve preset/cloning engine compatibility validation

##### Item 4

###### File

app/src/lib/api/types.ts

###### Lines

1. 1

###### Purpose

Review generated/manual request type compatibility if API schema changes

##### Item 5

###### File

scripts/generate-api.sh

###### Lines

1. 1

###### Purpose

Review schema/type generation workflow before regenerating artifacts

#### Recommended implementation

Default the optional engine field to None so omission follows the existing resolver: explicit request engine, profile.default_engine, profile.preset_engine, then qwen. Keep engine/profile validation intact.

#### Repair steps

1. Add request/route tests for omitted engine and explicit null, using preset and cloned profiles and a fake inference backend.

2. Change the Pydantic default for GenerationRequest.engine from qwen to None. Keep the existing enum/pattern validation for non-null values.

3. Retain qwen only as the final resolver fallback for profiles without configured defaults. Do not remove explicit request override precedence.

4. Verify both /generate and /generate/stream use the same resolver and choose a compatible model_size where applicable.

5. Review OpenAPI/generated request types and documented minimal requests. Explicit qwen clients must continue behaving as before.

#### Invariants

1. Omitted and explicit-null engine use profile metadata when available.

2. Explicit non-null request engine remains the first choice, subject to compatibility validation.

3. A profile with no engine metadata still resolves to qwen.

4. Preset profiles cannot silently run a different engine, and invalid engine strings are rejected.

#### Acceptance criteria

1. Minimal profile_id/text requests for Kokoro and CustomVoice preset profiles resolve to their preset engine without an unintended qwen validation error.

2. A cloned profile defaulting to a supported non-qwen cloning engine uses that engine on omission.

3. Explicit qwen, explicit null, incompatible preset overrides and missing-profile cases preserve documented behavior.

4. Request schema/type changes do not make existing explicit-engine callers invalid.

#### Regression cases

1. Engine omitted, engine=null, explicit matching engine, explicit incompatible preset engine and invalid engine string.

2. Preset with default_engine set; preset with only preset_engine; cloned profile with non-qwen default; profile without default metadata.

3. Both persisted and streaming endpoints resolve the same engine for the same profile/request.

4. Existing frontend clients that send their selected engine continue using it.

#### Compatibility risks

1. Clients that omitted engine and unintentionally relied on always using qwen will now honor the profile default. This intended behavior change should be called out in release notes.

2. Optional-null API types may differ across generated clients. Check generated OpenAPI/types rather than changing unrelated frontend selection logic.

3. Model-size defaults were not separately validated against every engine; run preset-engine endpoint tests to avoid loading an inappropriate model variant.

#### Rollback

##### Code

Revert the request-default change and related schema artifacts together. No data migration is needed.

##### Fallback

Callers explicitly supply a matching engine or null until the corrected default can ship.

##### Data

Do not rewrite persisted historical generation.engine values as part of this fix.

### VB-4 Medium: Stream generation silently discards the voice profile effects chain

**Status:** OPEN. [Pinned source](https://github.com/wwongw21/voicebox/blob/51f49dea198384b4eb6087b72c17057c6eb1c1cd/backend/routes/generations.py#L382).

#### Evidence classification

Confirmed

#### Observed mechanism

stream_speech gets profile from services.profiles.get_profile, which returns _profile_to_response (profiles.py:277). That converts stored effects JSON into List[EffectConfig] (profiles.py:35-42; models.py:39). The stream fallback calls json.loads(profile.effects_chain), catches the resulting TypeError, and sets effects_chain_config=None.

#### Impact and scope

POST /generate/stream without an explicit request effects_chain always ignores enabled profile default effects, while persisted /generate correctly loads effects from the database JSON. Streaming audio does not match the configured voice.

#### Repair direction

Use [effect.model_dump() for effect in profile.effects_chain] for the already-deserialized response, or share effects resolution with the persisted generation flow.

#### Verification already obtained

Constructed an actual VoiceProfileResponse with enabled reverb EffectConfig and verified the exact json.loads operation raises TypeError because the value is a list. The route catches this and skips apply_effects. Reproduction is in /workspace/review/voicebox_repro.py.

#### Validation already performed

Constructed an actual VoiceProfileResponse with enabled reverb EffectConfig and verified the exact json.loads operation raises TypeError because the value is a list. The route catches this and skips apply_effects. Reproduction is in /workspace/review/voicebox_repro.py.

#### Affected workflows

1. POST /generate/stream relying on profile effects defaults

2. API clients expecting streamed and saved voice effects to agree

#### Change surface

##### Item 1

###### File

backend/routes/generations.py

###### Lines

1. 379

2. 398

###### Purpose

Effects precedence and serialization for streamed WAV generation

##### Item 2

###### File

backend/services/profiles.py

###### Lines

1. 30

2. 62

3. 259

4. 278

###### Purpose

Profile response is already parsed into EffectConfig instances

##### Item 3

###### File

backend/models.py

###### Lines

1. 31

2. 39

3. 100

4. 101

###### Purpose

Response effects type and explicit-request override contract

##### Item 4

###### File

backend/services/generation.py

###### Lines

1. 175

2. 233

###### Purpose

Persisted generation effects behavior used for parity checks

##### Item 5

###### File

backend/utils/effects.py

###### Lines

1. 1

###### Purpose

Consumer expects serialized effect dictionaries; inspect validation/application behavior before changing caller

#### Recommended implementation

Use the typed profile effects list directly, serializing each EffectConfig with model_dump rather than passing the list through json.loads. Preserve request override, explicit empty-list disablement and no-effects semantics.

#### Repair steps

1. Add a streaming route regression where get_profile returns a real VoiceProfileResponse with enabled default effects and apply_effects is instrumented.

2. Replace the erroneous JSON parse of profile.effects_chain with serialization from its typed objects. Keep JSON parsing at the DB/profile-conversion boundary only.

3. Preserve data.effects_chain is not None as the override check so an explicit empty list disables profile defaults.

4. Verify enabled/disabled flags and params survive serialization and effects are applied once before the currently established normalization step.

5. If sharing a small effects-resolution helper with persisted generation, keep it limited to precedence/type conversion. Do not fold in unrelated synthesis or versioning refactors.

#### Invariants

1. Profile effects defaults are applied when request effects are omitted/null and a valid default chain exists.

2. Explicit request effects override profile defaults; explicit [] means no effects.

3. No effects are applied more than once, and parameter values/order/enablement are preserved.

4. Corrupt stored JSON remains handled at the profile deserialization boundary; the route must not silently swallow a type-contract programming error.

#### Acceptance criteria

1. Streaming endpoint calls apply_effects with the expected profile chain when no request override is supplied.

2. Explicit request chain replaces the profile chain and [] skips application.

3. Profile with no default effects still streams valid WAV without invoking effect processing.

4. For deterministic fake inference, persisted and streamed flows resolve the same effective chain; real audio smoke test confirms the intended audible result.

#### Regression cases

1. Profile effects None/empty/one enabled effect/multiple ordered effects with mixed enabled flags.

2. Request chain omitted/null/explicit chain/explicit empty list.

3. Effect parameters contain supported scalar values and survive model_dump unchanged.

4. Normalization on/off preserves established processing order and valid WAV response headers.

5. Malformed legacy profile effects JSON is handled by existing profile conversion behavior, not accidentally reintroduced into the stream parser.

#### Compatibility risks

1. Streaming output will intentionally change for voices whose default effects were previously ignored.

2. Effects can alter duration, amplitude or timbre; preserve existing processing order and validate clipping/normalization with representative audio.

3. A generalized helper should not reinterpret [] as missing or mutate the profile/request chain.

#### Rollback

##### Code

Revert the stream effects-resolution patch if required; this restores the known missing-effects behavior and does not close the finding.

##### Fallback

Send effects explicitly or use persisted /generate.

##### Data

No profile DB or generated audio migration is required; do not rewrite existing files.

## Branch

main

## Sha

51f49dea198384b4eb6087b72c17057c6eb1c1cd

## Local checkout

/workspace/repos/voicebox

## Handoff type

Implementation-ready review handoff; proposed repairs only

## Desired outcome

Preserve serial model execution during cancellation, round-trip audio paths under supported custom storage roots, honor profile engine defaults, and make streaming audio honor default effects. Close findings only after maintained regressions and relevant runtime checks pass.

## Scope and authority

### Reviewed

1. Backend app factory, CORS and lifespan setup, route registration and main/server startup contracts

2. Generation orchestration, task cancellation, PyTorch/MLX inference thread boundaries and streaming route

3. Profile CRUD and validation, ZIP import/export paths, storage path conversion and file serving

4. History/version persistence, story joins and editing interfaces, model cache migration/delete paths

5. Desktop permissions/configuration, frontend API request defaults, package/dependency manifests, Docker bindings and CI/release workflows

### Instructions

No AGENTS.md found in checkout.

### Performed

Source inspection and safe local validation. Review artifacts were written outside the checkout. No repository source, Git history, remote settings, issues or release artifacts were modified.

### Implementation authorization

Not granted. Everything below is a handoff proposal, not an applied repair.

### Evidence base

1. voicebox.json

2. voicebox_repro.py

3. voicebox_production_cancellation_repro.py

### Confidence limits

1. No full TTS/STT model weights, Torch installation or GPU runtime used; concurrency reproduction proves overlap of threads, not actual GPU exhaustion.

2. No Tauri desktop/native audio capture or packaging run on this Linux review host; no Bun or Rust compilation executed.

3. No dependency CVE/advisory scan; manifests and integration contracts were inspected statically.

4. Findings are repository source review, not full formal security audit; API auth/CORS was inspected but no browser exploit was run.

5. Repository source unmodified; review scripts live outside the checkout.

6. Follow-up cancellation validation executes source-extracted unchanged production async function bodies and real queue/chunker with fake model and DB/audio dependencies; does not establish actual GPU crash severity. The default UI source exposes active cancellation, but no desktop/browser click test was run.

### Finding count

4

### Severity counts

#### High

1

#### Medium

3

## Verification debt

### Item 1

#### Id

VB-VD-1

#### Status

OPEN

#### Classification

Verification debt, not another runtime finding

#### Title

Progress tracker test expects callbacks for a fixture intentionally below the reporting threshold

#### Evidence

##### Item 1

###### File

backend/tests/test_progress.py

###### Lines

1. 142

2. 149

###### Purpose

Fixture total_size=1000 and assertion requiring callbacks

##### Item 2

###### File

backend/utils/hf_progress.py

###### Lines

1. 145

2. 154

3. 304

4. 311

###### Purpose

Both tracking paths intentionally suppress totals below 1,000,000 bytes

#### Diagnosis

The isolated test fails identically under huggingface-hub 2.2.0/tqdm 4.70.1 and huggingface-hub 0.36.0/tqdm 4.67.3. An actual tracker probe emits 0 callbacks for a 1,000-byte total and 1 callback for a 2,000,000-byte total. The inconsistency is the existing fixture/assertion, not evidence of broken large-file progress tracking.

#### Proposed repair

1. Use a fixture total at/above the intentional threshold for the positive callback test.

2. Add a separate below-threshold assertion that no progress callback is emitted.

3. Exercise both standard tracked tqdm and the supported HuggingFace progress hook at bounded dependency versions.

4. Keep terminal complete/error SSE behavior covered independently from byte progress.

#### Acceptance criteria

1. Positive and below-threshold tests describe the intentional behavior and pass.

2. Selected lightweight suite has no unexplained failure.

3. No runtime threshold change is introduced solely to make the old 1,000-byte fixture pass.

#### Rollback

Test-only change can be reverted without affecting runtime, but the known stale failure returns.

### Item 2

#### Id

VB-VD-2

#### Status

OPEN

#### Classification

Verification coverage gap

#### Title

Configured default CI does not run the backend regression suite

#### Evidence

##### Item 1

###### File

.github/workflows/ci.yml

###### Lines

1. 9

2. 26

###### Purpose

frontend-quality job installs Bun, typechecks app/web and builds web only

#### Proposed repair

1. After runtime repairs are authorized, add a lightweight deterministic backend regression lane with supported Python/dependency versions.

2. Keep model-download/live-GPU tests in a separate explicit matrix with clear availability requirements.

3. Resolve package/import prerequisites before claiming backend suite coverage.

#### Limitations

No CI execution was performed by this reviewer. Root reported zero workflow runs on the fork; configuration presence is not proof of a successful run.

## Reproduction and validation

### Item 1

#### Name

Four baseline bug-mechanism probes

#### Status

ALREADY RUN; baseline defect assertions passed

#### Cwd

/workspace

#### Prerequisites

1. Checkout at pinned SHA available as /workspace/repos/voicebox

2. Review artifact /workspace/review/voicebox_repro.py

3. Python 3.12.14; observed installed pydantic 2.13.4 and numpy 2.3.5

#### Command

```
PYTHONPATH=/workspace/repos/voicebox python /workspace/review/voicebox_repro.py
```

#### Observed

1. Stored voicebox/generations/example.wav resolves to a doubled .../data/voicebox/voicebox/generations/example.wav path.

2. Second queued job starts while cancelled first thread remains blocked.

3. Omitted request engine becomes qwen despite a kokoro profile default.

4. json.loads on profile.effects_chain raises TypeError because it is a list.

#### Safety and limits

Uses temporary files and fake threaded work, no model weights or remote writes. The installed NumPy 2.3.5 is outside the repository runtime numpy<2 bound; these probes demonstrate stdlib/Pydantic/type/lifetime mechanisms, not compatibility of a full production model environment.

#### After repair

These scripts intentionally assert the current defect. A future fix should make the defect assertion fail; convert expected repaired behavior into maintained repository regressions instead of treating a passing defect probe as a release gate.

### Item 2

#### Name

Unchanged production cancellation path challenge

#### Status

ALREADY RUN; overlap confirmed

#### Cwd

/workspace

#### Prerequisites

1. Same checkout and Python/NumPy environment

2. Review artifact /workspace/review/voicebox_production_cancellation_repro.py

#### Command

```
PYTHONPATH=/workspace/repos/voicebox python /workspace/review/voicebox_production_cancellation_repro.py
```

#### Observed

1. Unchanged AST-extracted run_generation returned, recorded failed through a fake DB and closed its session before the first model thread finished.

2. The actual generate_chunked and task_queue modules plus unchanged PyTorch async generate method started and completed the second fake model call before the first was released.

3. Statuses before first release: first generating; first failed; second generating; second completed.

#### Safety and limits

AST extraction avoids importing Torch/weights while preserving inspected production async bodies. Fake model blocks on a thread event; DB/audio/backend lookup are doubles. Demonstrates ownership overlap, not an actual GPU OOM or native frontend click.

### Item 3

#### Name

Python syntax inspection

#### Status

ALREADY RUN; passed

#### Cwd

/workspace/repos/voicebox

#### Command

```
python - <<'PYCODE'
import ast
from pathlib import Path
files = list(Path("backend").rglob("*.py"))
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print(len(files), "files parsed")
PYCODE
```

#### Observed

120 backend Python files parsed; zero syntax errors.

#### Limits

Parsing is not an import, startup, model, lint or type check.

### Item 4

#### Name

Selected lightweight backend suite

#### Status

ALREADY RUN; 33 passed, 1 failed

#### Cwd

/workspace/repos/voicebox

#### Prerequisites

1. Python 3.12 and uv

2. Network/package cache for listed lightweight dependencies

3. PYTHONPATH includes both backend and repository root because test_progress imports utils directly

#### Command

```
PYTHONPATH=/workspace/repos/voicebox/backend:/workspace/repos/voicebox uv run --no-project --with pytest --with pytest-asyncio --with sqlalchemy --with fastapi --with httpx --with tqdm --with huggingface-hub python -m pytest backend/tests/test_task_queue_cancellation.py backend/tests/test_audio_failed_generation.py backend/tests/test_cors.py backend/tests/test_progress.py -q --disable-warnings
```

#### Observed

33 passed, 1 failed in 4.03s. Sole failure test_hf_progress_tracker is explained in VB-VD-1.

#### Versions observed

##### Python

3.12.14

##### Pytest

9.1.1

##### Huggingface hub

2.2.0

##### Tqdm

4.70.1

#### Limits

uv command resolved unbounded lightweight packages at review time, not a complete production dependency lock. Current Hub 2.2.0 is not established as compatible with all repository model packages. No complete backend suite or model weights were installed.

### Item 5

#### Name

Isolated stale progress test under older dependencies

#### Status

ALREADY RUN; same expected stale fixture failure

#### Cwd

/workspace/repos/voicebox

#### Prerequisites

1. Python 3.12 and uv

#### Command

```
PYTHONPATH=/workspace/repos/voicebox/backend:/workspace/repos/voicebox uv run --no-project --with pytest --with pytest-asyncio --with huggingface-hub==0.36.0 --with tqdm==4.67.3 python -m pytest backend/tests/test_progress.py::test_hf_progress_tracker -q --disable-warnings
```

#### Observed

1 failed in 1.14s; 0 progress callbacks for the 1,000-byte fixture, identical to current dependency behavior.

#### Limits

This isolates the fixture/threshold inconsistency; it does not certify either full model dependency environment.

### Item 6

#### Name

Actual tracker threshold probe

#### Status

ALREADY RUN; threshold behavior confirmed

#### Cwd

/workspace/repos/voicebox

#### Prerequisites

1. Python 3.12 and uv; current probe environment used huggingface-hub 2.2.0 and tqdm 4.70.1

#### Command

```
PYTHONPATH=/workspace/repos/voicebox/backend:/workspace/repos/voicebox uv run --no-project --with pytest --with pytest-asyncio --with sqlalchemy --with fastapi --with httpx --with tqdm --with huggingface-hub python - <<'PYCODE'
from importlib.metadata import version
from backend.utils.hf_progress import HFProgressTracker
print('huggingface-hub', version('huggingface-hub'), 'tqdm', version('tqdm'))
for size in (1000, 2000000):
    reports = []
    tracker = HFProgressTracker(lambda *args: reports.append(args))
    with tracker.patch_download():
        from tqdm import tqdm
        with tqdm(total=size, desc='model.bin', unit='B', disable=True) as progress:
            progress.update(size)
    print('Total bytes:', size, 'callback count:', len(reports))
PYCODE
```

#### Observed

1. Total bytes: 1000 callback count: 0

2. Total bytes: 2000000 callback count: 1

#### Limits

No download or model inference takes place. The tracker force-enables its own accounting, so a terminal progress display can still appear despite disable=True in the probe.

### Item 7

#### Name

Collection limit for MCP middleware tests

#### Status

NOT COMPLETED

#### Command

```
Initial selected test command also included backend/tests/test_client_id_middleware.py
```

#### Observed

Collection failed because fastmcp was not installed. Test excluded from later lightweight run.

#### Limits

Do not count this middleware test as passed or as a confirmed runtime defect.

## Release validation not run

### Item 1

#### Check

Production dependency environment

#### Status

NOT RUN

#### Required evidence

Install the repository-supported full backend environment on each supported target; record exact versions and dependency conflicts. Lightweight uv environments above are not release qualification.

### Item 2

#### Check

Real GPU cancellation

#### Status

NOT RUN

#### Required evidence

On at least PyTorch/CUDA and Apple Silicon/MLX where supported, start a long inference, cancel through the actual UI, queue another and capture non-overlapping model execution, memory behavior and eventual cancellation status.

### Item 3

#### Check

All supported adapter cancellation seams

#### Status

NOT RUN

#### Required evidence

Maintained fake-model tests for each adapter/helper implementation and representative actual engine smoke tests; include model load/prompt creation cancellation.

### Item 4

#### Check

Repeated cancel and sidecar shutdown

#### Status

NOT RUN

#### Required evidence

Desktop close/reopen, watchdog/parent exit, repeated cancellation and backend restart complete without stranded processes, permanently active rows or generation overlap.

### Item 5

#### Check

Custom data-root user workflow

#### Status

NOT RUN

#### Required evidence

On disposable copied data under roots containing earlier data components, create profile/sample/avatar/capture/generation versions, restart, play/export each file and verify all references. Run any approved legacy recovery dry-run and targeted rollback.

### Item 6

#### Check

Engine-default endpoint integration

#### Status

NOT RUN

#### Required evidence

Real minimal API requests for Kokoro/CustomVoice presets and a non-qwen cloned default use the intended engine; explicit override/null/error cases remain correct.

### Item 7

#### Check

Actual effects audio parity

#### Status

NOT RUN

#### Required evidence

Stream and save deterministic/representative audio using profile defaults and overrides; verify expected effects, WAV validity and normalization/clipping behavior.

### Item 8

#### Check

Frontend/native build and packaging

#### Status

NOT RUN

#### Required evidence

Bun install/typecheck/web build, Rust/Tauri packaging and supported OS native capture/playback flows pass with the repaired backend.

### Item 9

#### Check

Complete backend/MCP suite

#### Status

NOT RUN

#### Required evidence

Install required imports including fastmcp, run applicable deterministic suite with clear separation of tests requiring a running server/model/GPU, and retain results.

### Item 10

#### Check

CI/release execution and rollback rehearsal

#### Status

NOT RUN

#### Required evidence

Execute authorized CI/release gates, stage signed artifacts as applicable, test application rollback and any path-record reversal on disposable copied data before production rollout.

## Completion conditions

1. All four findings remain OPEN in this handoff. Implementation, commits, remote writes and releases require separate authorization.

2. Close each defect only with an applied repair, maintained expected-behavior regression, relevant integration evidence and documented compatibility/rollback results.

3. Keep stale progress-test debt separate from runtime finding counts.

4. A passing parse/probe/lightweight suite is not a claim of full GPU, desktop, dependency-security or release coverage.

## Delivery and diagnostic files

### Destination repository

wwongw21/voicebox

### Status

All findings remain OPEN; documentation transfer only.

### Portable evidence

This JSON retains the complete repository-specific handoff. Historical /workspace paths are evidence references and prerequisites, not portable hyperlinks. Applicable repository-specific diagnostics are included alongside the report.

### Diagnostics included

1. voicebox_repro.py

2. voicebox_production_cancellation_repro.py

### Not included

Original environments, model weights, user data and complete source snapshots are not included. Clone the pinned source and follow the recorded prerequisites. Bug-demonstration scripts intentionally assert pre-fix behavior; they are not a post-fix passing suite.

- [voicebox_repro.py](diagnostics/voicebox_repro.py)
- [voicebox_production_cancellation_repro.py](diagnostics/voicebox_production_cancellation_repro.py)

## Remaining review limits

1. No full TTS/STT model weights, Torch installation or GPU runtime used; concurrency reproduction proves overlap of threads, not actual GPU exhaustion.

2. No Tauri desktop/native audio capture or packaging run on this Linux review host; no Bun or Rust compilation executed.

3. No dependency CVE/advisory scan; manifests and integration contracts were inspected statically.

4. Findings are repository source review, not full formal security audit; API auth/CORS was inspected but no browser exploit was run.

5. Repository source unmodified; review scripts live outside the checkout.

6. Follow-up cancellation validation executes source-extracted unchanged production async function bodies and real queue/chunker with fake model and DB/audio dependencies; does not establish actual GPU crash severity. The default UI source exposes active cancellation, but no desktop/browser click test was run.

