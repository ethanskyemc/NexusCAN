# Workbench v1 — Phase C1 (team-lead contract)

Status: **DRAFT — scope agreed with user 2026-11-19 (Phase C split into C1 / C2); details to confirm at C1 start.**
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
    firmware per `file` input key. Bytes are NEVER stored; chosen at run time; CRC mismatch refuses the run
    (operator may explicitly "accept different file" → recorded in the run record `errorMessage: null`, file ref
    shows the actual CRC; the expectation is not silently updated).
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
   `durationMs` = `Math.round`; log excerpt ≤ 200 × 512). Append failure = visible error, run result still shown.
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

## 8. Open questions for the user at C1 start
1. Workbench as a Diagnostics sub-tab (proposed) or a top-level area?
2. "Accept different file" on CRC mismatch: allowed (recorded) or never allowed?
3. Operator name in run records: free text set once per session (proposed) or empty until Cloud identity?
