# Diagnostics Workspace v1 (rev 2) — ECU Registry, Flow Interfaces, Workbench, Run Store (team-lead contract)

Status: **APPROVED by user (2026-11-18)**. Supersedes rev 1 (same file). Overrides `flow-platform-v1.md`
where §8 / §9 below say so; otherwise `flow-platform-v1.md`, `seedkey-v1.md`, `uds-client-d003-v1.md` still apply.

Frozen code contracts (change = update BOTH sides + notify team-lead):
- TS: `src/types/diagWorkspace.ts`, `src/types/flowLibrary.ts`, `src/types/runRecord.ts`
- Rust mirror: `crates/nexus-hal/src/flowlib.rs`, `crates/nexus-hal/src/runstore.rs` (serde camelCase)

## 1. Principles (user decisions, 2026-11-18)
1. **ECU-centric.** The diagnostics area exists to work on ECUs: *register first, then operate*.
   With no ECU registered the area shows one empty state: "ECU Ekle / ECU Bul / Simüle Et".
2. **Layers are strictly separated:** Project → Vehicle → Group → ECU → (protocol + params).
   - Group = organisation only (flat in v1, no behaviour, no inheritance).
   - ECU = identity + **manually chosen protocol** + protocol params. No protocol prober.
3. **Flow Editor is protocol-agnostic** (renamed from "UDS Flow"). UDS is one block pack among
   `core`, `can`, `uds`, later `kwp`, `obd`, `j1939`, `canopen`, `xcp`.
4. **Every flow has an interface** derived from its `flow_input` / `flow_output` blocks. A saved flow or
   custom block is callable as a block in another flow, to any depth (flow-platform-v1 §6 semantics).
5. **Workbench** = matrix ECU × bound flows: run a cell / row / column / selection, see last result per cell.
6. **Reporting is the goal:** every run produces an immutable `RunRecordV1` in an append-only run store,
   outside the project file. Everything is **NexusCAN Cloud-ready**: stable UUIDs, revisions, immutable runs.

## 2. Where things live
| Data | Location | Cloud model |
| :--- | :--- | :--- |
| Vehicles, groups, ECU records, flow bindings | project file `NexusProjectFileV1.diagnostics` | editable doc (`id` + `revision` + `updatedAt`) |
| Flows, custom blocks (all revisions) | app flow library `<data_root>/flowlib/` (backend) | editable doc, immutable revisions |
| Seed/key profiles | `seedkey_profiles.json` (seedkey-v1) | native paths are machine-local, never synced |
| Run records | `<data_root>/runs/<projectId>/<YYYY-MM>.jsonl` (backend, append-only) | immutable event, upload-only, dedupe by `runId` |
| Loaded firmware bytes, live queue state | memory only | never |

## 3. Project file (additive, `version` stays 1)
```ts
interface NexusProjectFileV1 { /* existing fields unchanged */
  projectId?: string;                    // uuid; generated on load when missing, then saved
  diagnostics?: DiagnosticsProjectV1;    // missing / invalid → EMPTY_DIAGNOSTICS + import notice, never throws
}
```

## 4. Registry DTOs — `src/types/diagWorkspace.ts` (authoritative, summary here)
- `DiagnosticsProjectV1 { schemaVersion: 1; vehicles; activeVehicleId }`
- `DiagVehicleV1 { id, name, notes, groups, ecus, bindings, revision, createdAt, updatedAt }`
- `EcuGroupV1 { id, name, color, order }` — flat, names unique per vehicle.
- `EcuRecordV1 { id, name, groupId|null, order, notes, source, protocol, connectionProfileId|null, addressing,
  timing, security, routines, flash, identification, dids, vars, revision, createdAt, updatedAt }`
  - `protocol: DiagProtocol` — v1 only `'uds_can'` is operable; others reserved.
  - `security: { seedKeyProfileId | null; levels: { key, label, level }[] }` — **reference only**: the project
    never contains a provider spec, DLL path, process or URL (removes rev 1 §8 approval mechanism).
  - `routines`, security levels, memory regions, DIDs, vars are addressed by a **key** (`[A-Za-z][A-Za-z0-9_]{0,31}`),
    never by the display label.
- `FlowBindingV1 { id, ecuId, flowRef, label|null, params, order }` — per ECU. "Bind to group" is a UI shortcut
  creating one binding per member ECU (no inheritance, no exclude lists).
- `FlowRefV1 { flowId, revision }` — pinned; "update to latest" is explicit and lists affected bindings.

## 5. `{ecu.*}` references (closed list, anything else = error)
| Path | Value |
| :--- | :--- |
| `ecu.name` | text |
| `ecu.routines.<key>` | 4 hex |
| `ecu.security.level` / `ecu.security.levels.<key>` | 2 hex (first level / by key) |
| `ecu.security.profileId` | seed/key profile id (`""` when null) |
| `ecu.flash.alfid` / `.dfi` / `.eraseAlfid` | 2 hex |
| `ecu.flash.maxBlockLen` | decimal, `0` when null |
| `ecu.flash.regions.<key>.start` / `.length` | 8 hex |
| `ecu.ident.expectedHw` / `.expectedSparePart` | text, `""` when null |
| `ecu.ident.vinDid` / `.swDid` / `.hwDid` / `.sparePartDid` | 4 hex |
| `ecu.vars.<key>` | text |

Pure module `src/components/diagnostics/workspace/ecuRefs.ts`: `listEcuRefs(config)`,
`resolveEcuRefs(config, ecu) → { config, errors }`. Resolved once at run start against the flow's `ecu` input;
unresolved → run refused before any TX. `ecuSnapshotHash(ecu)` = sha-256 of canonical JSON (for run records).

## 6. Validation — `src/components/diagnostics/workspace/ecuValidation.ts` (pure, localized, field-addressed)
- IDs 0..=0x7FF (11-bit) / 0..=0x1FFFFFFF (29-bit); request ≠ response; addressFormat rules = `sessionValidation.ts`.
- **Collision key:** (connectionProfileId, isExtendedId, addressFormat, requestId, targetAddress) and the same with responseId /
  responseAddress. Two ECUs with `connectionProfileId: null` are compared as if on the same bus.
- u8: padding, dfi, alfid, eraseAlfid, targetAddress, responseAddress; alfid nibbles 1..4.
- Security levels odd 0x01..=0x7D, unique; keys unique per collection; RIDs / DIDs exactly 4 hex;
  regions non-overlapping, `length > 0`, `start + length <= 2^32`.
- ECU names unique per vehicle (trim, case-insensitive); group names unique per vehicle; `groupId` exists or null.

## 7. `.nexusecu.json` exchange
`NexusEcuFileV1 { schemaVersion: 1; kind: 'nexuscan.ecus'; exportedAt; groups; ecus }`. Import regenerates ids,
suffixes clashing names " (2)", user picks target vehicle. `seedKeyProfileId` that does not exist locally is
cleared and listed in the import notice. Bindings are not exported.

## 8. Flow Editor changes (overrides flow-platform-v1)
- Rename: tab "UDS Flow" → **"Flow Editor"**, tab id `uds_flow` → `flow`, dir `src/components/diagnostics/uds-flow/`
  → `src/components/flow/`. i18n keys renamed accordingly.
- `BlockDef` gains `pack: BlockPack` and `requires?: DiagProtocol[]`; palette groups by pack; a block whose
  `requires` does not contain the bound ECU's protocol fails validation before the run.
- `PortType` gains `'ecu'`.
- **`flow_input`** (exactly one per flow, replaces legacy `start`): config `inputs: FlowPortSpecV1[]`; output ports:
  exec `start` + one data/ecu port per input. Legacy `start` blocks migrate to `flow_input` with `inputs: []`.
- **`flow_output`** (0..N): config `{ name, outcome: 'ok' | 'fail' | 'error', outputs: FlowPortSpecV1[] }`; input
  ports: exec `in` + one per output. Reaching it ends the frame with that outcome and output values.
  A flow without `flow_output` ends `ok` when the exec path finishes, `error` on an unhandled error.
- Interface: `deriveFlowInterface(flow) → FlowInterfaceV1` (pure, `src/types/flowLibrary.ts`). Replaces
  `FlowParameterV1`: run parameters ARE the `flow_input` inputs.
- `{ecu.*}` resolves against the input with key `ecu` (type `'ecu'`). A flow using `{ecu.*}` or any block with
  `requires` must declare it. *(B2 decision: enforced only for `{ecu.*}` use; `requires` is checked when an ECU is
  bound, so legacy/template flows without an `ecu` input still run on the session. Computed warning levels
  (flow-platform-v1 §4) do not exist in code yet → open item for Phase C.)*
- Call block: `type: 'call:<flowId>'`, `config.ref: FlowRefV1`; ports = callee interface. Both library kinds
  (`nexuscan.flow`, `nexuscan.block`) are callable; `nexuscan.block` items appear in the palette.
  Recursion guard, call depth limit 64, Stop / Panic Stop propagation: flow-platform-v1 §6.
- Vehicle flows: block `ecu_source` (config `ecuId`) outputs an `ecu` value, wired into a call block's `ecu` input.

### 8.1 Core pack completeness (user requirement 2026-11-18: "basit bir şeyi bile yapamazken sunamayız")
Found gap: `data_var` assigns as a side effect of being *read* (`set_in`), there is no arithmetic block, and
`x → +1 → x.set_in` is rejected as a cycle — a "count to 100 and log" flow is impossible today.
Rules: **reads are pure, writes happen only on the exec path.** Variables are scoped to the call frame.
| Block | Kind | Ports / config |
| :--- | :--- | :--- |
| `var_get` | pure | out `value`; config `name` (select from declared variables) |
| `var_set` | exec | `exec_in`, `value_in`, `exec_out`; config `name`, `mode: 'set' \| 'add' \| 'sub'` (add/sub = numeric increment) |
| `var_declare` panel | flow-level | variable list in the flow inspector: `{ name, type: number \| text \| bool \| bytes, initial }`; reset at frame start; unknown name = validation error |
| `math` | pure | `a_in`, `b_in` (optional → config `b`), op `+ − × ÷ mod min max abs round floor`; ÷0 = run error |
| `logic` | pure | `a_in`, `b_in`, op `and or xor not` → bool |
| `convert` | pure | `value_in` → number / text / hex text / bytes (config: byte length, endianness) |
| `text_format` | pure | template `"x = {x}, nrc = {nrc}"` with variables + named inputs → text |
| `flow_for` | exec | config `from`, `to`, `step` (or inputs); outs `body_out`, `done_out`, data `index`; safety cap 100 000 |
| `flow_break` / `flow_continue` | exec | exits / skips the nearest enclosing `loop` / `flow_while` / `flow_for` |
| `log` | exec | `message` supports the same `{name}` templates as `text_format` |
- Migration: legacy `data_var` with `set_in` unconnected → `var_get` + declared variable (initial kept); with
  `set_in` connected → `var_get` + a `var_set` inserted on the exec path before the first reader, or (if no safe
  insertion point) the flow loads with a validation error pointing at the block. Never silent behaviour change.
- **Golden example flows** (shipped as templates AND executed headless in tests, simulated and `flowReal` engines):
  1. count `x` from 0 while `x < 100`, log `"x = {x}"` each step → exactly 100 log lines `x = 0 … x = 99`;
  2. `flow_for` 1..10 with `flow_break` at 5 → 5 iterations;
  3. sum 1..100 via `var_set add` → 5050;
  4. read DID F190 (sim ECU) → `convert` to text → `text_format` → log.

## 9. Flow library (backend storage, overrides flow-platform-v1 §3 location)
`<data_root>/flowlib/<id>/r<revision>.json` immutable revision files + `<data_root>/flowlib/index.json`.
| Command | Args | Result |
| :--- | :--- | :--- |
| `cmd_flowlib_list` | `{ includeDeleted: boolean }` | `FlowLibEntryDto[]` |
| `cmd_flowlib_revisions` | `{ id }` | `FlowLibRevisionDto[]` |
| `cmd_flowlib_load` | `{ id, revision: number \| null }` (null = latest) | `string` (file JSON) |
| `cmd_flowlib_save` | `{ fileJson }` | `FlowLibEntryDto` |
| `cmd_flowlib_delete` | `{ id }` | `null` (soft delete; revisions kept so pinned refs still load) |
- Save: envelope `{ schemaVersion: 1, kind: 'nexuscan.flow' | 'nexuscan.block', flow: { id, revision, name, ... },
  contentHash }`; `revision` must equal latest + 1 (first save = 1) else `[flowlib:conflict]`; an existing revision file
  is never overwritten; ≤ 2 MiB; id `[A-Za-z0-9_-]{1,64}`. Revision write = tmp + fsync + hard-link to `r<N>.json`
  (link fails if it exists; rename would silently overwrite on POSIX), create-new fallback; index = tmp + rename.
  Soft delete = `<id>/deleted` marker file (survives index rebuild).
- Errors: `"[flowlib:<code>] <message>"`, codes `invalid`, `conflict`, `not_found`, `too_large`, `io`.

## 10. Run store (backend, append-only)
| Command | Args | Result |
| :--- | :--- | :--- |
| `cmd_run_append` | `{ record: RunRecordV1 }` | `null` |
| `cmd_run_query` | `{ projectId, filter: RunQueryFilterDto, limit }` (limit 1..=5000) | `RunRecordV1[]` newest first |
| `cmd_run_latest` | `{ projectId }` | `RunRecordV1[]` latest per (ecuId, flowId), top-level runs only |
- One JSON line per record, file `runs/<projectId>/<YYYY-MM>.jsonl` from `startedAt`; append + flush + fsync.
- Validation: schemaVersion 1, uuid-safe `runId` / `projectId`, ISO-8601 timestamps, record ≤ 256 KiB,
  `logExcerpt` ≤ 200 lines × 512 chars. Never rewrites or deletes lines. Corrupt lines are skipped on read.
- Errors: `"[runstore:<code>] <message>"`, codes `invalid`, `too_large`, `io`.
- Firmware appears only as `{ key, name, size, crc32 }`.

## 11. Safety
- An ECU record or binding never implies TX: Listen-Only / Arm Active Bus / Panic Stop unchanged.
- Workbench queue runs flash flows **strictly sequentially**; read-only flows (computed warning level ≤ info)
  may run in parallel on different ECUs (Phase C). Stop policy: always stop the queue when an ECU is left in the
  bootloader; Panic Stop cancels everything.

## 12. Phases
| Phase | Scope | Owner | Gate |
| :--- | :--- | :--- | :--- |
| **A1** | §3–§7: registry store, project persistence + migration, validation, `{ecu.*}` resolver, "ECU Kayıt" tab, empty state, "Oturum Aç" from ECU, `.nexusecu.json` | frontend-dev | tsc + `test:diagws` |
| **A2** | §9 flow library + §10 run store: nexus-hal modules, Tauri commands, bridge, permissions | backend-dev | `cargo test -p nexus-hal`, clippy, tauri check |
| **B0** | §8.1 core pack (variables, math, logic, convert, text_format, for/break/continue, log templates) + migration + golden example flows | frontend-dev | `test:flowcore` (4 golden flows, both engines) + `test:flasher` unchanged |
| **B** | §8: Flow Editor rename + packs + `flow_input`/`flow_output` + interface + `ecu` port + library client (migrate localStorage) + call blocks (nested frames) + `{ecu.*}` in engine | frontend-dev | `test:flasher` unchanged + nesting/interface tests |
| **C** | Workbench matrix, bindings, queue, run records, report export (HTML/CSV), vehicle flows (`ecu_source`) | frontend-dev (+ backend-dev review) | queue tests |
| **D** | HIL: 3 × `nexus-ecu-sim` on vcan0, queued flash, bootloader fault injection, run records verified | qa-hil-verifier | all green |
