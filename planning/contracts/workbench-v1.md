# Workbench v1 — Phase C1 (team-lead contract)

Status: **DRAFT — scope agreed with user 2026-11-19 (Phase C split into C1 / C2); §8 decisions answered 2026-11-19.**
Builds on `diag-workspace-v1.md` (§4 bindings, §5 `{ecu.*}`, §10 run store, §11 safety) and
`flow-interface-v2.md` (Flow = Block, §8 Workbench outline). Those stay authoritative where not overridden here.

## 0. Starting a new session (handoff)
Read in this order: this file → `flow-interface-v2.md` (§1, §2, §8, §10) → `diag-workspace-v1.md` (§4, §5, §10, §11).
State at handoff: commit `c2087fe` on `development` (flow interface v2 Phase 1, QA PASS). Token budget matters:
keep the team-lead context small, give frontend-dev the QA probes up front ("do not deliver until these pass").

## 1. Role split (user decision)
Flow Editor = block workshop (author, Test Run, version). **Workbench = operation:** pick ECU(s), pick blocks,
fill parameters, run, see results and run records. Production runs and reports come only from the Workbench.

## 2. Scope
| | C1 (this contract) | C2 (later, separate approval) |
| :--- | :--- | :--- |
| Matrix | rows = ECUs of the active vehicle (grouped, `order`), columns = distinct bound blocks | column/row multi-select |
| Binding | add/edit/remove binding per ECU, "bind to group" shortcut (one binding per member ECU), binding form | bulk edit |
| Run | **one cell at a time** (Run cell), Stop, Panic Stop | sequential queue for row / column / selection, parallel read-only runs |
| Results | cell shows last run outcome + outputs; cell click → run record detail (params, files, outputs, log excerpt) | history list per cell, filters |
| Records | every Workbench run appends `RunRecordV1` via `cmd_run_append`; cells load from `cmd_run_latest` | report export HTML / CSV |
| Vehicle flows | — | `ecu_source` block, multi-ECU procedures |

## 3. Files / ownership
- **frontend-dev:** new `src/components/diagnostics/workbench/**`; `DiagnosticsView.tsx` (new sub-tab `workbench`,
  placed right after `ecu_registry`); `diagWorkspaceStore.ts` (binding CRUD if missing); new
  `src/services/runStoreClient.ts` (typed `invoke` wrappers for `cmd_run_append|query|latest` + browser-bridge
  fallback consistent with `flowLibClient.ts`); i18n shard `src/i18n/shards/workbench.ts` (tr + en).
- **backend-dev:** only if `cmd_run_*` needs a fix (commands exist in `src-tauri/src/commands.rs` /
  `app_commands.rs`); verify `capabilities/default.json` allows them.
- **No change** to `crates/nexus-hal/src/runstore.rs` DTOs unless a §5 field is impossible to express (then stop
  and report to team-lead).

## 4. Data
- `FlowBindingV1` (existing, project file): `{ id, ecuId, flowRef, label, params, order }`.
  - **Additive:** `fileExpect?: Record<string, { fileName: string; crc32: string /* 8 hex upper */ }>` — expected
    firmware per `file` input key. Bytes are NEVER stored; chosen at run time; **CRC mismatch always refuses the
    run** (no "accept different file" override in C1 — §8.2). Changing the expectation = editing the binding.
  - `params[key]` = literal (`string | number | boolean`) **or** a string that is exactly one `{ecu.<path>}`
    reference (resolved per row ECU with the existing `resolveEcuRefs`; unresolvable = cell shows the error,
    run refused). Update `diagnosticsSanitize.ts` + `ecuValidation.ts` accordingly; old files load unchanged.
- **Bindable block** = library item whose derived interface has an `ecu` input (flow-interface-v2 §1.6). The
  `ecu` input is never shown in the binding form; it is the row ECU.
- **Binding form** is generated from `FlowInterfaceV1.inputs` (order, label, type, unit/min/max, byteLength,
  options, accept) — same field components as `RunInputsDialog` (extract/share, do not duplicate). Required
  inputs without value → binding saved but cell shows "incomplete", run disabled.
- **Pinned revision:** binding keeps `flowRef.revision`. If the library has a newer revision, the column header
  shows a notice; "Update to latest" lists affected bindings, re-validates params against the new interface
  (removed keys dropped with a visible note, new required keys → incomplete), one undo step.

## 5. Running a cell (C1)
1. Preconditions (all checked BEFORE any TX, refusal shown in the cell + toast):
   binding complete and valid; block revision loads; ECU protocol operable; params resolved; files chosen and CRC
   checked; no other Workbench run active; a diagnostics session for that ECU exists and is connected (offer
   "Oturum Aç" via existing `openSessionFromEcu`; never auto-arm — Listen-Only / Arm Active Bus unchanged).
2. Review gate: if the block's subtree contains a flash / erase / write block (until computed warning levels
   exist: any `uds_flash_image`, `uds_routine_ctrl` with routine `FF00`/erase, `uds_request_download`,
   `uds_write_memory`, `uds_write_did`), show a confirm dialog listing those blocks with their path
   (`Flash ECU › Programming › Erase FF00`), ECU name, addressing, firmware name + CRC.
3. Run with a **dedicated engine instance** (not the Flow Editor canvas): root body = the pinned library
   revision, `ecu` = row ECU, inputs = resolved params + files. Verify `FlowEngine` / `RealFlowExecutor` do not
   fall back to `flowStore` (canvas) for the diagram or block statuses when a root body is given; fix by
   injection if they do (with a test).
4. Stop / Panic Stop: Workbench Stop stops this run; global Panic Stop must stop it too (one code path).
5. Record: build `RunRecordV1` (outcome map: run outcome `ok|fail|error` → same; stopped by user → `aborted`;
   Panic Stop → `panic_stop`; `outputName` = reached `flow_return` name; numeric/text outputs as `RunValue`,
   bytes → hex string; files → `RunFileRefV1`; `ecuSnapshotHash` = sha-256 of canonical ECU record;
   `durationMs` = `Math.round`; log excerpt ≤ 200 × 512; `operator: null` in C1 — §8.3). Append failure = visible error, run result still shown.
6. Cell display: outcome chip (`ok` nominal, `safe_abort`/other `fail` warn, `bootloader_fail` **critical with
   text "ECU bootloader'da olabilir"**, `error` critical), short outputs, timestamp. `font-mono tabular-nums`.

## 6. UI rules
Industrial dark HMI, no animations / backdrop-blur, no `any`. Matrix virtualized when > 200 rows. Empty states:
no vehicle / no ECU (reuse `EcuEmptyState`), no bindable block in library ("Flow Editor'da `ecu` girişi olan bir
blok kaydedin").

## 7. Tests / gate (C1)
- New `src/components/diagnostics/workbench/workbench.test.ts` (+ `test:workbench` script), headless, no Tauri:
  binding form generation from an interface; `{ecu.*}` param resolution per ECU incl. error; incomplete binding;
  update-to-latest param reconciliation; CRC mismatch refusal with zero TX; run of the `ecu_flasher` template
  revision via the dedicated engine with a fake transport → record built with correct outcome / outputName /
  files / durationMs integer; Stop → `aborted`, Panic Stop → `panic_stop`; canvas store untouched during a run.
- QA writes its probes FIRST (`workbench.qa.test.ts`, `test:workbench-qa`) and hands them to frontend-dev.
- Gate: `npx tsc -p tsconfig.app.json --noEmit`, `test:workbench`, `test:workbench-qa`, all existing flow +
  diag tests green; `cargo test -p nexus-hal` only if backend touched.

## 8. Decisions (user, 2026-11-19: "sadece 1 olsun şimdilik")
1. **Workbench = Diagnostics sub-tab** (`DiagSubTab` `workbench`, right after `ecu_registry`). ✅ in C1.
2. "Accept different file" on CRC mismatch: **not in C1** — a mismatch always refuses the run. May be revisited later.
3. Operator name in run records: **not in C1** — `operator: null`. May be revisited later (or filled by Cloud identity).

## 9. Implementation API (team-lead, 2026-11-19 — FROZEN for QA probes + frontend-dev)
Backend check: `cmd_run_append|query|latest` registered (`lib.rs`, `app_commands.rs`) and allowed by permission set
`workspace` (`capabilities/default.json`) → **no backend work in C1**.

### 9.1 `src/types/diagWorkspace.ts` (additive)
`FlowBindingV1.fileExpect?: Record<string, FileExpectV1>`; `export interface FileExpectV1 { fileName: string; crc32: string }`
(8 hex upper). Sanitizer drops malformed entries (crc not `/^[0-9A-F]{8}$/` after upper-casing) with a visible note.

### 9.2 `src/components/flow/FlowEngine.ts` (additive)
`FlowRunOptions.rootBody?: FlowDiagram` — when set the engine runs this diagram as the root frame and **never reads
or writes `flowStore`** (diagram, block statuses, run log, selection) during that run; log lines go to
`FlowRunOptions.onLog?: (line: string) => void`. Without `rootBody` behaviour is unchanged (canvas).

### 9.3 `src/components/diagnostics/workbench/workbenchModel.ts` (pure: no React, no Tauri, no stores)
```ts
export type WbFieldKind = 'number' | 'text' | 'bool' | 'bytes' | 'hex' | 'select' | 'file';
export interface WbFormField { key: string; label: string; kind: WbFieldKind; required: boolean; order: number;
  unit?: string; min?: number; max?: number; byteLength?: number; options?: { value: string; label: string }[];
  accept?: string[]; defaultValue?: string | number | boolean | null }
export function isBindableInterface(iface: FlowInterfaceV1): boolean;          // has an input of type 'ecu'
export function buildBindingForm(iface: FlowInterfaceV1): WbFormField[];       // inputs order, 'ecu' excluded
export type WbParamIssue = { key: string; code: 'missing' | 'type' | 'range' | 'ecu_ref' | 'unknown_key'; message: string };
export type WbResolved =
  | { status: 'ready'; params: Record<string, RunValue> }                      // non-file inputs, resolved
  | { status: 'incomplete'; missing: string[]; issues: WbParamIssue[] }        // required w/o value (incl. file w/o fileExpect)
  | { status: 'invalid'; issues: WbParamIssue[] };                             // bad literal / unresolvable {ecu.*}
export function resolveBindingParams(binding: FlowBindingV1, iface: FlowInterfaceV1, ecu: EcuRecordV1): WbResolved;
export interface WbReconcileResult { binding: FlowBindingV1; dropped: string[]; newlyMissing: string[] }
export function reconcileBindingToRevision(binding: FlowBindingV1, next: FlowInterfaceV1, nextRevision: number): WbReconcileResult;
export function crc32Hex(bytes: Uint8Array): string;                           // IEEE, 8 hex upper, wraps flowAlgos.crc32
export type WbFileCheck = { ok: true; ref: RunFileRefV1 } | { ok: false; key: string; expected: string; actual: string };
export function checkFileExpectation(binding: FlowBindingV1, key: string, file: FlowFileValue): WbFileCheck;
export interface WbReviewItem { path: string; blockType: string }              // path "Flash ECU › Programming › Erase FF00"
export function reviewItems(body: FlowDiagram, rootName: string, loadCallee: (ref: FlowRefV1) => FlowDiagram | null): WbReviewItem[];
export type WbCellView = { chip: 'none' | 'ok' | 'warn' | 'critical'; text: string };  // bootloader_fail → critical + i18n "ECU bootloader'da olabilir"
export function cellView(rec: RunRecordV1 | null): WbCellView;
export function mapOutcome(r: { outcome: 'ok' | 'fail' | 'error'; stopped: boolean }, stopKind: 'user' | 'panic' | null): RunOutcome;
export interface WbRecordInput { runId: string; projectId: string; vehicleId: string | null; ecu: EcuRecordV1;
  flowId: string; flowRevision: number; flowContentHash: string; flowName: string; params: Record<string, RunValue>;
  files: RunFileRefV1[]; startedAtMs: number; endedAtMs: number; result: FlowRunResult; stopKind: 'user' | 'panic' | null;
  log: string[]; deviceId: string; appVersion: string; ecuSnapshotHash: string }
export function buildRunRecord(i: WbRecordInput): RunRecordV1;  // durationMs Math.round, bytes→hex upper, log ≤200×512 (tail), operator null, parentRunId/queueId null
export function ecuSnapshotHash(ecu: EcuRecordV1): Promise<string>; // sha-256 hex of canonicalJson(ecu) (ecuRefs.ts)
```

### 9.4 `src/components/diagnostics/workbench/workbenchRunner.ts` (no React; deps injected)
```ts
export interface WbRunDeps {
  loadRevision(ref: FlowRefV1): Promise<{ body: FlowDiagram; iface: FlowInterfaceV1; name: string; contentHash: string } | null>;
  loadCallee(ref: FlowRefV1): FlowDiagram | null;
  transportFor(ecu: EcuRecordV1): FlowTransport | null;   // null = no connected session → refuse 'no_session'
  isRunActive(): boolean;                                   // another Workbench run active → refuse 'busy'
  confirmReview(items: WbReviewItem[], ecu: EcuRecordV1, files: RunFileRefV1[]): Promise<boolean>;
  appendRecord(rec: RunRecordV1): Promise<void>;
  now(): number; uuid(): string; deviceId: string; appVersion: string; projectId: string; vehicleId: string | null;
}
export type WbRefusal = { code: 'incomplete' | 'invalid' | 'revision_missing' | 'protocol' | 'crc_mismatch' | 'busy'
  | 'no_session' | 'review_declined'; message: string };
export interface WbRunHandle { stop(): void; done: Promise<WbRunResult> }
export type WbRunResult = { kind: 'refused'; refusal: WbRefusal }                      // zero TX guaranteed
  | { kind: 'ran'; record: RunRecordV1; appendError: string | null };
export function startWorkbenchRun(binding: FlowBindingV1, ecu: EcuRecordV1,
  files: Record<string, FlowFileValue>, deps: WbRunDeps): WbRunHandle;
```
Order: §5.1 preconditions (all, before transport is touched) → review gate (only if `reviewItems` non-empty) →
dedicated `new FlowEngine().run({ rootBody, ecu, inputs, transport, resolveCallee, rootId, rootName })`.
`stop()` → engine.stop(), stopKind `user`; global Panic Stop reaches the engine via the existing tx-source registry
(`stopAllTxSources`) → stopKind `panic` (runner detects it through a hook exported by `store/txSources` or by
registering itself as a tx source — one code path, no second Panic Stop implementation).

### 9.5 Other files
`src/services/runStoreClient.ts` (`runAppend/runQuery/runLatest`, browser-bridge in-memory fallback like
`flowLibClient.ts`); `src/store/workbenchStore.ts` or a slice in `diagWorkspaceStore.ts` (latest records per
`(ecuId, flowId)`, active run handle); UI `src/components/diagnostics/workbench/{WorkbenchPanel,WorkbenchMatrix,
BindingForm,RunRecordDetail,ReviewDialog}.tsx`; shared field components extracted from `RunInputsDialog.tsx`.

## 10. Amendments
- 2026-11-19 (team-lead, QA probe review — §9 ambiguities resolved):
  1. `{ecu.*}` into a `number` input: resolved string is parsed (dec or `0x` hex) and range-checked; non-numeric →
     `type`, out of [min,max] → `range`. Never clamped.
  2. `reviewItems`: any `uds_routine_ctrl` whose routine id is an `{ecu.*}` reference is flagged (conservative; the
     path shows the reference). `custom` routine id `FF00` (case-insensitive) counts as erase.
  3. `WbRefusal.code` gains `'file_missing'` (required file input not chosen at run time) and `'stopped'`
     (Stop / Panic Stop before the engine started, e.g. while the review dialog is open). Both: zero TX, no record.
  4. `unknown_key` (param key not in the interface): reported as an issue, does NOT change status, never in params;
     the UI shows it as a note.
  5. Required `text` / `hex` / `bytes` with `""` = missing.
  6. `durationMs = Math.max(0, Math.round(endedAtMs - startedAtMs))`.
  7. `reconcile.dropped` lists removed param keys AND removed `fileExpect` keys (deduped, sorted).
  8. File name differs but CRC matches → allowed; record uses the chosen file's name; UI shows a non-blocking note.
  9. `mapOutcome({ outcome: 'error', stopped: true }, null)` → `'aborted'`.
  10. `WbResolved` `ready` variant gains `notes: WbParamIssue[]` (always present, `[]` when none) carrying
      `unknown_key` (and any other non-blocking note) for the UI.
- 2026-11-19 (team-lead, C1 delivery review — accepted deviations): store file is `src/store/diagWorkbenchStore.ts`
  (`workbenchStore.ts` was taken by the explorer dock); matrix windowing is in-house (no virtualizer dependency);
  `reviewItems` flags the real type `uds_req_download` as well; `loadCallee` stays sync — callees are preloaded in
  `loadRevision`; Workbench refuses to run in Diagnostics DEMO mode and without `projectId` (no fabricated production
  records); `appVersion` is a constant until a build-time version exists; `deviceId` = persisted UUID.
  Known C2 follow-up: `FlowEngine.stop()` also cancels global flash runs / prompts, so a Workbench Stop cancels a Flow
  Editor flash in flight — scope it per engine before C2 parallel runs.
- C1 gate PASS (2026-11-19): tsc clean; test:workbench 32/32; test:workbench-qa 14/14 probes (251 checks); flasher,
  flowcore, flowreg, flownest, terminals, flowiface, flowiface-qa, diagws, decode, traffic, graph, inflight, flash, hwlo green.
  Backend untouched. UI not yet exercised in a running app.
