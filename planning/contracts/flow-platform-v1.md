# Flow Platform v1 — Persistence, AI Authoring, Warning Levels (team-lead contract)

> **Amended 2026-11-18 by `diag-workspace-v1.md` rev 2:** "UDS Flow" → protocol-agnostic **Flow Editor**
> (block packs); `FlowParameterV1` replaced by `flow_input` / `flow_output` blocks and the derived
> `FlowInterfaceV1`; library storage moves to the backend (`cmd_flowlib_*`) with immutable revisions; run history
> becomes the append-only run store (`cmd_run_*`). Types: `src/types/flowLibrary.ts`, `src/types/runRecord.ts`.

Status: APPROVED by user (2026-11-17). Implementation starts after the detailed ECU-flash flow work lands
(it changes `blockDefs.ts` / `FlowEngine.ts` / `templates.ts`, which this contract builds on).

## 1. Decisions (user)
- **Sharing = NexusCAN Cloud (later).** No Git / project-folder sharing. Flows live in a local app library now;
  the format is cloud-sync-ready from day one so the Cloud phase only adds a sync layer.
- **AI-authored flows** carry per-block warning levels shown as a static glow on the block; the AI output
  explains its levels.
- Copy/paste AI workflow (no API key, offline-safe): app builds prompt → user pastes it into any AI →
  user pastes the JSON back → app validates → loads.

## 2. File format `.nxflow.json` (schemaVersion 1)
```ts
interface NxFlowFileV1 {
  schemaVersion: 1;
  kind: 'nexuscan.flow';
  flow: {
    id: string;              // stable UUID, never changes (cloud identity)
    revision: number;        // +1 on every save
    name: string; description: string;
    createdAt: string; updatedAt: string;  // ISO-8601 UTC
    author: string | null;   // local display name; cloud fills real identity later
    origin: 'user' | 'template' | 'ai' | 'import';
    aiReviewed: boolean;     // origin 'ai' → false until the user confirms the review screen
    tags: string[];
    parameters: FlowParameterV1[];   // run-time inputs (file, hex, number, text, select)
    blocks: NxFlowBlockV1[];
    connections: FlowConnection[];   // existing shape
  };
  contentHash: string;       // sha-256 over canonical JSON of `flow` minus revision/updatedAt/contentHash
}
interface NxFlowBlockV1 {
  id: string; type: string; label: string; x?: number; y?: number;   // x/y optional → auto-layout
  config: Record<string, unknown>;
  ai?: { warningLevel: WarningLevel; reason: string };                // only from AI output
}
type WarningLevel = 'none' | 'info' | 'warn' | 'high' | 'critical';
interface FlowParameterV1 { key: string; label: string; type: 'file'|'hex'|'number'|'text'|'select'; required: boolean; default?: unknown; options?: string[] }
```
Runtime-only fields (`status`, `statusMessage`, `lastResult`, loaded file bytes, image ids) are NEVER serialized.
Migration: loader accepts the legacy `FlowDiagram` from localStorage and upgrades it (origin 'user', revision 1).

## 3. Storage
- Local library in the app data dir (Tauri) / IndexedDB fallback in browser-bridge mode; one file per flow,
  atomic write. Library index: id, name, revision, updatedAt, origin, aiReviewed, contentHash.
- Explicit Export / Import of single `.nxflow.json` files is allowed (backup), but is not the sharing mechanism.
- Run history per flow: runId, flowId, revision, contentHash, parameters (file name + CRC only, never file bytes),
  tab addressing, start/end, result, error lane taken, log excerpt. Capped (e.g. last 200 runs).

## 4. Warning levels (static glow)
- Effective level = max(computed, ai.warningLevel). Computed is authoritative and derived in the app from
  block type + config + request options, e.g.:
  - critical: 0x31 FF00/erase routines, `uds_flash_image`, 0x34/0x36/0x37, 0x3D write memory, 0x11 during programming
  - high: 0x2E, 0x28 (comm off), 0x85 (DTC off), 0x14, 0x27, 0x10 02, 0x2F IO control, functional-addressed writes
  - warn: 0x10 03, 0x11 outside programming, 0x31 other routines, keepalive
  - info: reads (0x22, 0x19), data/log/io blocks → none
- Rendering: static `box-shadow` / outline using `--status-warn` (warn), `--status-critical` (high/critical, critical
  thicker), `--status-focus` (info). NO animation, NO pulse, NO backdrop-blur. Tooltip shows computed reason +
  AI reason. Applies to ALL flows (not only AI) so the operator always sees risk; AI flows additionally show the AI reason.
- AI flows (`origin:'ai' && !aiReviewed`) cannot run until the review screen (list of high/critical blocks with
  bytes/addressing) is confirmed; confirmation sets aiReviewed=true and bumps revision.

## 5. AI prompt generator
- Generated, never hand-maintained: block catalogue from `blockDefs.ts` (type, purpose, ports with direction/type,
  config fields with type/options/defaults, ISO 14229 reference), the JSON schema above, hard rules
  (unique ids, every error_out/fail_out/timeout_out wired, safe-abort vs bootloader error lanes for flashing,
  functional addressing only with SPR where applicable, include `ai.warningLevel` + `reason` on every block),
  one compact example (the `ecu_flasher` template), then the user's request.
- Import validator: unknown type/port, exec↔data port mismatch, missing required config, bad hex, dangling
  error outputs, cycles without a loop/while block → line-level errors; nothing loads until valid.

## 6. Custom (subsystem) blocks — ARBITRARY NESTING (user requirement)
Implementation comes later (design doc together with scripting), but the v1 format MUST already support it so
nothing breaks later:
- A custom block is stored in the same library as `kind: 'nexuscan.block'` with the same envelope as §2
  (`id`, `revision`, `contentHash`, origin, aiReviewed) plus `interface`: exposed ports
  (`{ id, label, direction, type: 'exec'|'bool'|'data'|'bytes', optional }`) and parameters, and an inner
  `blocks` + `connections` graph whose boundary nodes are `subsystem_in` / `subsystem_out` port blocks.
- A custom block may contain other custom blocks to **any depth**. Referencing is by
  `type: 'custom:<blockId>'` + `ref: { blockId, revision | 'latest' }` (pinned revision by default, so an edit
  of an inner block never silently changes a validated flash flow; "update to latest" is an explicit action).
- **Recursion guard:** the validator builds the reference graph and rejects cycles (A→B→A, self-reference)
  with the full path in the error. No artificial depth limit in the format; the runtime has a safety
  limit on call depth (e.g. 64) and on total expanded node count, reported as a clear error.
- **Execution semantics:** each custom block instance runs as a nested call frame (not textual copy):
  own variable scope, inputs mapped from the parent's ports, outputs mapped back. Errors propagate to the
  parent's matching error port. Stop / Panic Stop / cancel propagate to every depth instantly; keepalives and
  flash runs started inside are cleaned up when their frame exits.
- **Warning level** of a custom block = max over its whole expanded subtree (computed, §4), so a "Prepare ECU"
  block that contains an erase is shown as critical at every level. The review screen lists risky blocks with
  their full path (e.g. `Flash ECU › Programming › Erase FF00`).
- **Logs / run history** are hierarchical (path per entry), and the UI navigates into a custom block with a
  breadcrumb (double-click to enter, path bar to go up).
- **Missing reference** (block deleted or not yet synced from Cloud): the flow still loads, the instance is
  shown as unresolved, and the flow cannot run until it is resolved.
- The AI prompt generator includes the user's custom blocks (interface + purpose, not their internals) so
  the AI can compose with them, and may emit new custom-block definitions in the same JSON (validated the same way).
- Script blocks (TypeScript, same runtime/API/safety gates as automation scripting) plug into the same
  interface model, so a script block and a subsystem block are interchangeable from the parent's view.
