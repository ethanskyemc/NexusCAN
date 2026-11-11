# UDS Interpretation Layer — Principles & License Policy (v1, DRAFT)

Status: principles accepted by user (2026-11-10). Detailed DTOs are written together with the D-003 contract.
Differentiator: NexusCAN does not stop at "received bytes / NRC name". It explains **what happened, why,
and what to do next**, with evidence. Same engine for active tester, passive (Listen-Only) analysis,
UDS Flow, UDS Panel and `nexus-cli` reports.

## 1. License policy — "lisanssız, en iyisi"
Project license: MIT. Every shipped artifact must be redistributable under MIT-compatible terms.

| Source | Policy |
|---|---|
| ISO 14229 / ISO 15765 / ISO 15031-6 / SAE J1979 / SAE J2012 **documents** | Never copy text or tables verbatim. Interoperability facts (SIDs, NRC codes, bit positions, formulas, DTC structure) are implemented; all names/explanations are **written by us**. |
| SAE J2012 DTC **descriptions** | **Not shipped.** Do not bundle community DTC lists of unclear origin (often derived from J2012). |
| DTC information we ship | Structural decode only: letter P/C/B/U, generic vs manufacturer-specific, subsystem group of the 3rd character with **our own short labels**, status byte bits, snapshot/extended record structure. |
| DTC / DID / routine descriptions | Come from the **user's own sources**: ODX/PDX import, CSV/JSON import, in-app annotations. Stored per project/ECU, exportable in an open JSON schema so teams can share. |
| ODX / PDX (ASAM MCD-2D, ISO 22901) | Import supported. Reference implementation and test oracle: **mercedes-benz/odxtools (MIT)**; example `somersault.pdx` (MIT, to be confirmed) as test data. We do not redistribute the ASAM specification. |
| CDD (Vector proprietary) | **Not supported.** Users export ODX from their authoring tool instead. |
| Rule base (explanations, guidance, insights) | Our own work = our IP and differentiator. |

Not legal advice: before commercial release, have the DTC category labels and ODX import reviewed by a lawyer.

## 2. Layers
1. **Decode** — service/sub-function, DID/RID names, typed fields with units, NRC, DTC structure & status bits.
2. **Context** — request↔response pairing, measured vs expected P2/P2*, session/security timeline, S3 countdown,
   0x78 chains, ISO-TP anomalies (STmin violation, missing CF, FC wait/overflow).
3. **Explanation (why)** — rule-based cause analysis using context
   (e.g. 0x33 after S3 expiry; 0x13 expected vs sent length; 0x73 expected vs sent block counter;
   0x7F with the session that supports the service).
4. **Guidance (what next)** — suggested actions (switch session + retry, start TesterPresent, lockout countdown,
   read precondition DIDs), insertable as UDS Flow steps.
5. **Insight** — DTC history & diff since last read, flash throughput/bottleneck/ETA, ECU identity card.
6. **Compare & report** — before/after, ECU vs ECU, exportable report + audit trail.

Passive "story mode": Listen-Only reconstruction of a third-party tester session as a readable narrative.

## 3. Engineering rules
- Engine in Rust: `nexus-protocols::uds::interpret` (shared by all consumers).
- Output is structured, never prose-only: decoded fields, severity, `explanationKey` + params (TR/EN i18n),
  suggested actions, **evidence** (frame indices + timestamps, clickable to Trace).
- Deterministic and rule-based. No guessing without evidence; unknown stays "unknown".
- Each rule has test vectors; `tests/hil/ecusim` fault scenarios (0x33, 0x78, lockout, S3) are the HIL oracle.
  When ODX is loaded, decode results are cross-checked against `odxtools decode`.
- Prerequisite for D-003: the UDS client keeps a **conversation log with timing and state** — a plain
  request/response API is not acceptable.
