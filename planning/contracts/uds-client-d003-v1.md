# Contract: UDS Client (D-003) — v1

Owner: team-lead. Approved by user 2026-11-11 ("NexusCAN'e UDS client ekleyelim, oradan test yapmak istiyorum").
Basis: spikes `spikes/automotive-eval` (49/49) and `spikes/automotive-hal-bridge` (all pass) — read their READMEs.

Frozen code contracts:
- `crates/nexus-protocols/src/uds/port.rs` — `DiagCanPort`, `DiagPortEvent`, `DiagTxReject`, `DiagTapFilter`
- `crates/nexus-protocols/src/uds/dto.rs` — IPC DTOs, `DiagError` (`"[diag:<code>] msg"`)
- `crates/nexus-protocols/src/uds/client.rs` — `UdsSession` public API (blocking, `Send + Sync`)
- `src/types/udsClient.ts` — TS mirror + `DiagCommandMap` + `parseDiagError`
Changing any of these requires team-lead approval and updating both Rust and TS.

## 1. Layering
```
HMI (Diagnostics tabs, UDS Panel)          src/            frontend-dev
  │ cmd_diag_* (Tauri + nexus_bridge, same names/args)
PipelineEngine diag manager                crates/nexus-hal  backend-dev
  │ UdsSession (one per DiagSessionConfigDto key)
nexus-protocols::uds::client               protocol-specialist
  │ automotive =0.6.0 (ISO-TP + UDSClient), adapter over DiagCanPort
nexus-hal diag tap (DiagCanPort impl)      backend-dev
  │ single bus-io owner thread: fan-out RX to StateTable + taps, gated TX
drivers (SocketCAN, mock0, SLCAN, ...)
```
Dependency direction: `nexus-hal → nexus-protocols → nexus-core`. `automotive` only in `nexus-protocols`,
`default-features = false` (its own adapters are NOT used).

## 2. HAL (backend-dev)
- `PipelineEngine::open_diag_tap(bus_id, filters: &[DiagTapFilter], depth) -> Result<impl DiagCanPort, DiagError>`.
  Bus-io owner thread keeps sole channel ownership; matching RX frames are copied (`Copy`, no heap) to tap queues;
  StateTable / 60 Hz telemetry unchanged (TX echoes of diag frames still reach StateTable/Trace).
- TX requests carry origin; `TxConfirmed`/`TxRejected` go only to the sending tap.
- Gate: Listen-Only / Disarmed / Panic latched checked early AND in the bus-io thread right before driver write.
  Panic Stop, disarm, mode change, disconnect → close all diag taps (`is_closed() == true`, pending sends rejected `Closed`).
- No heap allocation added on the bus-io hot path (prove with the spike's alloc counter approach or equivalent test).
- Diag manager: `HashMap<key, Arc<UdsSession>>`, key = (busId, requestId, responseId, isExtendedId, functionalId, useFd).
  Commands auto-open a session if missing (requires connected + armed bus, else `bus_not_connected` / `listen_only` / `disarmed`).
  A closed tap invalidates the session (next call reopens if gate allows).
- Commands (both `src-tauri` and `nexus_bridge`; bridge via `spawn_blocking`), see `DiagCommandMap`:
  `cmd_diag_open_session`, `cmd_diag_close_session`, `cmd_diag_session_info`, `cmd_diag_send_uds`
  (request PDU = `[sid] + [subFunction if not null] + data`), `cmd_diag_read_did`, `cmd_diag_read_stored_dtcs`
  (default `statusMask` 0xFF), `cmd_diag_set_tester_present`, `cmd_diag_get_conversation` (default max 500).
- Errors: `DiagError::to_ipc_string()`; bridge returns them through the existing `bad_request` path.

## 3. Client (protocol-specialist)
Productize the spike prototypes inside `UdsSession`:
- automotive `CanAdapter` implementation over `DiagCanPort` (TX confirmation from `TxConfirmed`, rejects → typed errors).
- Persistent per-ECU receiver + response matching (drop stale/late PDUs → `stale_response_dropped` event).
- Request serialization (one outstanding request; concurrent callers queue, `busy` only if queue wait > timeout).
- Background TesterPresent `3E 80` under the same lock, default interval 2000 ms.
- P2 client = ECU-reported P2 (from 0x50) or default 50 ms, + `p2MarginMs`; P2* from 0x50 or default 5000 ms; 0x78 handling.
- Functional addressing: request on `functionalId`, Flow Control on `requestId` (spike `SplitTransport`).
- Conversation log: ring buffer (≥ 10 000 events), HAL timestamps, every request/response/0x78/timeout/tx_blocked/
  stale drop/keepalive; P2 timeouts recorded by the wrapper.
- State tracking for `DiagSessionInfoDto`: active session from positive 0x50, unlocked level from positive 0x67 sendKey,
  reset to default/locked on 0x11 positive or S3-relevant session loss detected via NRC 0x7F/0x33 patterns (best effort, documented).
- Helpers: `read_did` (data record only, `decoded` ASCII-or-hex), `read_dtcs_by_status` (0x19 0x02, DTC code formatting
  `P/C/B/U` + 4 hex digits + `-FTB`, status bits 2/3/7). NRC → `negative_response` error for helpers; raw `request` → `Ok(positive=false)`.
- Our own NRC short names (license policy: own wording, ISO code facts only).
- Tests: unit tests (DTC formatting, NRC names, matching) + integration against `tests/hil/ecusim` over SocketCAN vcan0
  using a test-only `DiagCanPort` implementation (in `nexus-protocols` tests, NOT a HAL dependency).

## 4. HMI (frontend-dev)
- `mockMode` default **false**; when true, a visible "DEMO" badge in Diagnostics. Real mode uses only `cmd_diag_*`.
- Session tab: Connect → `cmd_diag_open_session`; Disconnect → `cmd_diag_close_session`; TesterPresent toggle →
  `cmd_diag_set_tester_present`; show `DiagSessionInfoDto` (active session, unlocked level, P2/P2*).
- UDS Console: `cmd_diag_send_uds`; show positive/negative (NRC name + code), 0x78 count, elapsed ms.
- History / log: poll `cmd_diag_get_conversation` (≈ 4 Hz while connected, `sinceSeq`) instead of locally fabricated entries.
- DID Inspector, DTC panel, UDS Panel (`udsTransport`) on the real commands; `readDidStrict` uses `raw` = data record only.
- Errors via `parseDiagError` → i18n messages (TR/EN) incl. "Arm Active Bus first" for `disarmed`/`listen_only`.
- Out of scope for v1 (keep, but show DEMO badge when mock): ISO-TP Monitor, Live PIDs, UDS Flow live target.

## 5. Verification (qa-hil-verifier, after 2–4)
Black-box against `tests/hil/ecusim` via a **separate** bridge instance (`NEXUS_BRIDGE_ADDR`) and separate ECU ids,
never touching the user's running instances. Scenarios: session/security (0x35/36/37), VIN multi-frame, DTC read/clear,
0x78, S3 + TesterPresent, Listen-Only/Disarmed/Panic gate, CAN-FD.
