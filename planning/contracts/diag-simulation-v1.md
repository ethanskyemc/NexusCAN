# Diagnostic Simulation v1 — UDS Panel → saved panels + OBD-II sources (team-lead contract)

Status: **DRAFT — user request 2026-11-19**: "UDS Panel alanını Simulation alanındaki gibi panel kaydetme şeklinde
getireceğiz; OBD-II fonksiyonlarından gelen verilerle de kullanabiliriz" + "UDS Panel ismini Diagnostic Simulation
şeklinde değiştirelim". Frontend only (no backend change).

## 1. Rename
- Every user-visible "UDS Panel / UDS Paneli" → **"Diagnostic Simulation"** (tr and en, same text): sub-tab label
  `diagHmi.tab.udsPanel`, traffic origin `diagTraffic.origin.panel`, default new panel name → `"Diagnostic Panel {n}"`.
- Internal ids (sub-tab id, store / file names `uds-panel/*`, i18n keys) stay unchanged for compatibility; no file moves.

## 2. Saved panels (parity with Simulation)
Today `udsInstrumentStore.ts` keeps `UdsPanelDto[]` in memory only. Target = Simulation behaviour
(`SimulationPanelListView.tsx` + `simulationPanelStore.ts` + `NexusProjectFileV1.simulationPanels`):
- **Project file (additive, `version` stays 1):** `NexusProjectFileV1.diagnosticPanels?: UdsPanelDto[]`.
  Loaded into the store on project open, written on project save; missing = `[]` (old files load unchanged).
  Sanitizer `src/components/diagnostics/uds-panel/udsPanelSanitize.ts` (pure): drops malformed widgets with a visible
  notice, clamps numbers, validates hex fields; never throws.
- Panel list view (reuse / generalise `SimulationPanelListView` — do not fork 700 lines): search, create, rename,
  duplicate, delete (confirm), export `.nexusdiagpanel.json`, import (sanitized), open. Per-panel saved snapshot →
  "unsaved changes" dot, `projectStore.dirty` set on edit, Save = project save.
- Design / Run mode unchanged. Run mode never starts polling or writes on load (explicit Run only); Listen-Only /
  Arm Active Bus / Panic Stop unchanged; write widgets keep their existing gates.

## 3. OBD-II data sources
- `UdsReadService` gains **`'obd_pid'`**: `UdsReadBinding.obdPid?: string` (2 hex, Mode 01 PID). Value = existing
  `PID_CATALOG` formula (`src/types/diagnostics.ts`); unit / min / max / decimals default from the catalog entry
  (overridable as for DIDs); PIDs not in the catalog → raw bytes with factor/offset (byteOffset/byteLen as for DIDs).
- Requests go through the existing diagnostics session transport used by `LivePidGauges` / `udsPollEngine`
  (Mode 01 `01 <pid>` on the session's request id; functional 0x7DF only if the session is configured functional).
  Poll scheduling shares `udsPollEngine` (one in-flight request per session, panel poll interval / widget `pollMs`).
- DEMO mode: values from the existing mock path, clearly marked DEMO as today.
- Binding UI (`UdsBindingSection.tsx`): service select shows "OBD-II PID (Mode 01)" with a PID picker listing the
  catalog (pid hex + name + unit) plus "custom PID".
- Optional nice-to-have if trivial: `'obd_vin'` (Mode 09 PID 02) for text widgets. Skip if it needs ISO-TP changes.

## 4. Files / ownership (frontend-dev)
`src/components/diagnostics/uds-panel/**`, `src/types/uds.ts`, `src/store/projectStore.ts` (field + load/save),
`src/components/simulation/SimulationPanelListView.tsx` (generalise only, Simulation behaviour unchanged),
`src/i18n/shards/diagnostics.ts`. Must not touch `crates/`, `src-tauri/`, `src/components/re/**`.

## 5. Tests / gate
New `src/components/diagnostics/uds-panel/diagSim.test.ts` + `test:diagsim`: sanitize (malformed widget dropped,
old project without field → `[]`), project round-trip (save → load → deep-equal), duplicate gives new ids,
`obd_pid` decode via catalog (e.g. PID 0x0C RPM `[0x1A,0xF8]` → 1726 rpm, PID 0x05 coolant `0x5A` → 50 °C),
unknown PID raw path, poll engine issues `01 0C` request on the session transport (fake transport) and never in Design mode.
Gate: `npx tsc -p tsconfig.app.json --noEmit`, `test:diagsim`, `test:diagws`, `test:workbench`, `test:decode`, `test:traffic`.
