# Flow Interface v2 — Simulink-style Ports, Bindable Parameters, Editor / Workbench Split (team-lead contract)

Status: **APPROVED by user (2026-11-19)** — "Birden fazla giriş ve birden fazla çıkış olmasını onaylıyorum."
Overrides `diag-workspace-v1.md` §8 (`flow_input` / `flow_output` bullets, `FlowInterfaceV1` shape) and §1.4/§1.5.
Everything else in `diag-workspace-v1.md` and `flow-platform-v1.md` §6 (nesting, pinning, recursion guard,
depth 64, Stop/Panic propagation) still applies.

## 1. Decisions (user, 2026-11-19)
1. **Flow = Block.** There is no separate "flow interface" concept: a flow's inputs/outputs ARE the block ports it
   gets when it is placed in another flow. At the root, input values come from the run form; inside a parent, from wires.
2. **Simulink-style ports:** one block per input (`flow_in`), one block per output (`flow_out`), any number of each.
   Exec semantics stay (this is an exec-flow engine, not pure dataflow): `flow_start` starts, `flow_return` ends
   with a named outcome.
3. **Any scalar block parameter can be exposed as an input port** ("Porta bağla") and promoted to a flow input
   ("Flow girişine yükselt") — this is what makes a saved ECU flasher reusable.
4. **Role split:** Flow Editor = block workshop (author, test-run, version). Workbench = operation (ECU × block
   matrix, binding form, queue, run records). Editor keeps a **Test Run** (simulated freely; real ECU only through
   the normal Arm Active Bus / Listen-Only / Panic Stop gates; recorded as `test` once Phase C run records exist).
5. **One ECU input per block in v1** (key `ecu`, type `ecu`, at most one). Multi-ECU procedures = Workbench queue.
   The format does not forbid more later.
6. Library kinds `nexuscan.flow` / `nexuscan.block` stay in the file format (no Rust change); semantics are identical,
   the kind only decides palette visibility. A block with an `ecu` input is Workbench-bindable.

## 2. Blocks (pack `core`, category `flow`)
| type | ports | config | rules |
| :--- | :--- | :--- | :--- |
| `flow_start` | out exec `start` | `{}` | exactly 1 per flow (0 = validation error "no entry") |
| `flow_in` | out `value` (port type = `portTypeOf(spec.type)`) | `FlowInSpecV2` | **pure** block; value = frame input `key`; keys unique among `flow_in`; at most one `ecu` |
| `flow_out` | in `value` (optional, same type rule) | `FlowOutSpecV2` | NOT on the exec path; sampled when the frame ends; keys unique among `flow_out` |
| `flow_return` | in exec `in` | `{ name: string; outcome: 'ok' \| 'fail' \| 'error' }` | 0..N; non-empty, `≠ 'in'`; the same name may repeat only with the same outcome (collapses to one call port); reaching it ends the frame |

```ts
// src/types/flowLibrary.ts (additive to FlowPortSpecV1; all new fields optional)
interface FlowPortSpecV1 {
  key: string; label: string; type: FlowPortValueType; required: boolean;
  default?: string | number | boolean; options?: string[];
  order?: number;          // port order on the call block / run form (ascending, ties → key)
  description?: string;    // tooltip + AI prompt
  unit?: string; min?: number; max?: number;   // number (run form + resolveRunInputs clamp check → error, no silent clamp)
  byteLength?: number;     // hex / bytes: exact length when set
  accept?: string[];       // file: extensions, e.g. ['.hex', '.s19', '.srec', '.bin']
}
type FlowInSpecV2 = FlowPortSpecV1;                       // flow_in.config
type FlowOutSpecV2 = Omit<FlowPortSpecV1, 'default' | 'options' | 'accept'>;  // flow_out.config

interface FlowInterfaceV1 {                // shape changes (still derived, never stored)
  inputs: FlowPortSpecV1[];                // from flow_in, sorted by (order, key)
  outcomes: { name: string; outcome: FlowOutcome }[];   // from flow_return; none → [{ name:'ok', outcome:'ok' }]
  outputs: FlowPortSpecV1[];               // from flow_out, sorted by (order, key) — flow-level, not per outcome
  requiresProtocols: DiagProtocol[];
}
```
`FlowOutcomeSpecV1.outputs` is removed. Call-block port ids are unchanged (input key / outcome name / output key /
always-present `error`), so parent wiring survives the migration.

### Runtime semantics
- `flow_in` reads `frame.inputValues[key]`; pure → usable anywhere, any number of readers.
- Frame end (any `flow_return`, or the exec path finishing = implicit `ok`): every `flow_out` is sampled with the
  existing `softInput` rule (source not run on this path → absent). A `required` output missing **when the outcome
  is `ok`** = run error (real engine); simulated engine stays lenient as today. Unhandled error / Stop → outcome
  `error`, outputs = whatever was sampled before (none on Stop).
- **File values:** a `file` input value is `FlowFileValue { name: string; bytes: Uint8Array }` (name is needed for
  format auto-detect). `resolveRunInputs` accepts `File`-derived `{name, bytes}`; `asBytes` of a `FlowFileValue`
  returns its bytes so existing `bytes` consumers keep working.

## 3. Bindable parameters ("Porta bağla" / "Flow girişine yükselt")
- Every `ConfigField` of type `text | textarea | number | select | hex | boolean` is bindable unless the field sets
  `bindable: false`. Not bindable: `variable`, `namelist`, `portspecs`, `code`. `uds_flash_image` additionally
  exposes the firmware file as a bindable pseudo-field `file` (type `file`, accept `.hex .s19 .srec .bin`).
- Block config stores `boundParams: string[]` (field keys). Each bound key adds one **required** input port
  `id = 'p:' + key`, label = field label, port type from the field: number/text/textarea/select/hex → `data`,
  boolean → `bool`, file → `bytes`.
- Execution (both engines): before the block runs, every bound port value is read, converted and validated against
  the field (number finite; hex = even-length hex, `byteLength` when the field implies one; select ∈ options;
  boolean) and **overrides** `config[key]` for that execution only (the stored config is untouched). Invalid /
  missing value = block error before any TX. For `uds_flash_image.file` the value is a `FlowFileValue` and the image
  is loaded from it (keyed by frame `pathKey` + block id, released when the frame exits / run ends).
- The inspector shows a bound field read-only with "← bağlı (p:key)" and an unbind button. Unbinding removes the
  port and its connection.
- **Promote:** context action on a field (and on an unbound `p:` port) → creates a `flow_in` (key = field key
  sanitised to `REGISTRY_KEY_RE`, de-duplicated with `_2`…; type mapped from the field; label = field label;
  default = current config value; select options copied; `accept` copied for files), binds the field, wires
  `flow_in.value → block.p:key`, places the `flow_in` left of the block. One undo step.
- `{ecu.*}` references in config stay as they are and are resolved before binding overrides.

## 4. Migration (load-time, `flowMigrate.ts`, deterministic, never silent)
Applied to the canvas, library loads AND callee bodies loaded by the engine/call index (so old pinned revisions
derive a v2 interface). Library revisions are immutable; the migrated form is saved only as a new revision.
1. `start` → `flow_start` (same id; `exec_out` connections → `start`).
2. `flow_input { inputs }` → `flow_start` (same id, `start` port kept) + one `flow_in` per spec
   (id `${oldId}__in_${key}`, x = old.x, y stacked below, config = spec + `order = index`); connections from
   `(old, key)` → `(newIn, 'value')`.
3. `flow_output { name, outcome, outputs }` → `flow_return { name, outcome }` (same id, `in` kept) + one `flow_out`
   per distinct output key across all old outputs (id `${firstOwnerId}__out_${key}`, `order` = first-seen index);
   connections into `(old, key)` → `(flow_out_key, 'value')`. If the same key had **different** sources or
   **different** types in different old outputs: keep the first, drop the others, and the flow loads with a
   validation issue `migrate_output_conflict` on the `flow_out` (cannot run until fixed).
4. `end` → `flow_return { name: 'ok', outcome: 'ok' }`.
5. `flow_terminal` / `flow_goto` / everything else unchanged. Old types (`start`, `end`, `flow_input`,
   `flow_output`) stay registered as `hidden` defs only so unmigratable data still renders.

## 5. Validation (pre-run, before any TX; also live in the editor)
- Exactly one `flow_start`; `flow_in` / `flow_out` keys valid + unique; reserved: input keys `≠ 'in'`,
  output keys ∉ outcome names ∪ {`in`, `error`}; one `ecu` input max, key `ecu`; select needs options;
  `min ≤ max`; `flow_return` names non-empty, same name ⇒ same outcome; the implicit `ok` outcome (no `flow_return`) reserves `ok` as an output key.
- `{ecu.*}` used ⇒ an `ecu` `flow_in` exists (rule unchanged, now looks at `flow_in`).
- `p:` ports must be connected (required); unknown `boundParams` key = error.

## 6. Templates
`ecu_flasher` and `ecu_flasher_minimal` are rebuilt on v2:
- Inputs: `ecu` (ecu, required, order 0), `firmware` (file, required, accept above, order 1), `baseAddr` (hex,
  byteLength 4, default `00000000`, order 2), `secLevel` (select `01 / 03 / 11`, default `11`, order 3).
  Bound: `uds_flash_image.file ← firmware`, `uds_flash_image.base_address ← baseAddr`,
  `uds_security_access.access_level ← secLevel`.
- Outcomes: `ok`, `safe_abort` (fail — aborted BEFORE the first erase / RequestDownload: application untouched, ECU
  restored to default session), `bootloader_fail` (fail — failed AFTER erase/download started: ECU may be in bootloader),
  `post_warning` (fail, post-programming lane,
  full template only), `error`. Outputs: `crc32` (number), `report` (text).
- All other templates: `start`/`end` replaced by `flow_start`/`flow_return ok` in source (no runtime migration needed).

## 7. Editor UX
- Palette "Arayüz" group: `flow_start`, `flow_in`, `flow_out`, `flow_return`.
- `flow_in` / `flow_out` render as compact Simulink-like port blocks: order badge, key, type chip
  (`font-mono tabular-nums`), no animation.
- Interface summary line (existing `in(…) → …`) now shows `in(ecu, firmware, baseAddr, secLevel) → ok · safe_abort · bootloader_fail · error | out(crc32, report)`.
- Test Run = existing `RunInputsDialog` (form generated from `flow_in` specs incl. order/unit/min/max/accept, ECU picker).

## 8. Workbench (Phase C, separate delegation — NOT in Phase 1)
- Rows = ECUs, columns = bindings (`FlowBindingV1`); `ecu` input supplied by the row.
- `FlowBindingV1.params` value may be a literal or an `{ecu.*}` reference string (resolved per row ECU);
  `file` inputs store `{ fileName, crc32 }` expectation only, bytes chosen at run time, CRC mismatch refuses the run.
- Newer revision available → column-header notice; update is explicit and lists affected bindings.
- Flash queue strictly sequential; review screen for high/critical; Arm Active Bus + Panic Stop always reachable.

## 9. Phases & gates
| Phase | Scope | Owner | Gate |
| :--- | :--- | :--- | :--- |
| **1** | §2–§7 (frontend only, `src/components/flow/**`, `src/types/flowLibrary.ts`, i18n) | frontend-dev | `npx tsc -p tsconfig.app.json --noEmit`, `npm run test:flowcore test:flasher test:flowreg test:flownest test:terminals test:diagws` all green + new v2 tests |
| **C** | §8 Workbench | frontend-dev (+ backend-dev if run store changes) | queue / binding tests |

## 10. Amendments
- 2026-11-19 (QA gate 1): duplicate `flow_return` names allowed with the same outcome; `crc32` output is `number`;
  migration `required` merge takes `required` only from old outputs whose outcome is `ok`; a legacy `start` is never
  converted when a `flow_start` already exists (idempotent migration); bound values whose DIRECT source is a `flow_in`
  or a `data_const` are validated pre-run (root frame; longer pure chains and callee bodies = follow-up) (before any TX of the whole run), not only at block execution;
  a flash block must honour Stop / Panic Stop during image load (`prepare`) — no download may start after Stop.
- 2026-11-19 (user): ecu_flasher templates get a separate `safe_abort` outcome (lane taken when the failure happens
  before the first erase routine / 0x34 RequestDownload); `bootloader_fail` only for failures after that point.
  Workbench (Phase C) shows `bootloader_fail` as "ECU may be in bootloader".
- 2026-11-19 (team-lead, QA gate 3): operator declining the confirmation prompt before anything was transmitted ends
  `safe_abort` directly (no restore frames). Stop / Panic Stop still ends `error` (stopped). Every exec error output
  after the first erase (incl. ECU reset) must reach `bootloader_fail` or `post_warning`; no dangling error outputs.
