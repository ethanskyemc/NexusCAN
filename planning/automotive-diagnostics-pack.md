# Automotive Diagnostics Pack — Uygulama Planı
## ISO-TP / OBD-II / UDS Entegrasyonu (Phase 3)

> **Toolpack ID:** `automotive_diagnostics`
> **Hedef Sürüm:** NexusCAN Phase 3
> **Ön Koşul:** `core` toolpack aktif, aktif bir bağlantı/kanal (SocketCAN, Mock veya USB-CAN)

---

## 1. Mimari Genel Bakış

Mevcut pipeline'a yeni bir katman olarak eklenir; **sıfır hotpath maliyeti** — toolpack kapalıyken herhangi bir CPU/bellek tüketimi olmaz.

```
Mevcut Mimari                        Yeni Eklenti
─────────────────────────────────    ───────────────────────────────────────────────
RawCanFrame ([u8; 64])               RawCanFrame → IsoTpEngine
       │                                   │            │
  StateTable (60Hz)              IsoTpSession     OBD2Service / UdsService
       │                                   │            │
  TelemetrySnapshotDto        DiagPayload       DiagResponseDto
       │                                   │            │
  TraceView / PlotterView      DiagnosticsView (React)
```

### Yeni Rust Crate: `crates/nexus-diag`

```
crates/nexus-diag/
├── Cargo.toml
└── src/
    ├── lib.rs                   # Genel API, DiagEngine, re-exports
    ├── isotp/
    │   ├── mod.rs               # IsoTpEngine — multi-frame state machine
    │   ├── frame.rs             # SingleFrame, FirstFrame, ConsecutiveFrame, FlowControl parser
    │   ├── session.rs           # IsoTpSession — TX assembly & RX defragmentation
    │   └── constants.rs         # N_PCItype, BS, STmin sabitleri
    ├── obd2/
    │   ├── mod.rs               # Obd2Service — request builder & response decoder
    │   ├── modes.rs             # Mode 01..09 enum, PID katalog yapısı
    │   ├── dtc.rs               # DTC (Pxxxx/Cxxxx/Bxxxx/Uxxxx) decoder
    │   └── pids.rs              # Standart OBD-II PID formül tablosu (Mode 01)
    ├── uds/
    │   ├── mod.rs               # UdsService — servis katmanı
    │   ├── services.rs          # 0x10..0x85 servis ID enum + request builders
    │   ├── nrc.rs               # Negative Response Code (0x7F) decoder
    │   ├── did.rs               # Standart DID tablosu (0xF190 VIN, 0xF186 ActiveSession…)
    │   └── dtc.rs               # UDS DTC status byte bit mask decode (0x19 sub-functions)
    └── dto.rs                   # DiagSessionDto, DiagResponseDto, DtcEntryDto, LivePidDto
```

---

## 2. ISO-TP Katmanı (`isotp/`)

### 2.1 Çerçeve Tipleri (ISO 15765-2)

| N_PCItype | Bit | İsim | Açıklama |
|-----------|-----|------|----------|
| `0x0` | [7:4]=0 | Single Frame (SF) | ≤7 byte, tek CAN frame'e sığan |
| `0x1` | [7:4]=1 | First Frame (FF) | >7 byte mesajın ilk parçası, uzunluk bilgisi |
| `0x2` | [7:4]=2 | Consecutive Frame (CF) | Devam paketleri, sequence number 0x1..0xF döner |
| `0x3` | [7:4]=3 | Flow Control (FC) | Alıcı → gönderici, BS ve STmin parametreleri |

### 2.2 IsoTpEngine State Machine

```rust
pub enum IsoTpSessionState {
    Idle,
    ReceivingMultiFrame {
        total_len: usize,
        buffer: Vec<u8>,        // max 4095 bytes (12-bit FF length)
        expected_sn: u8,        // expected consecutive frame sequence number
        deadline_us: u64,       // Cr timeout (N_Cr = 150ms default)
    },
    WaitingFlowControl {
        payload: Vec<u8>,       // TX buffer waiting for FC from tester
        block_size: u8,
        st_min_us: u32,
    },
    Complete(Vec<u8>),          // Assembled payload ready for upper layer
    Error(IsoTpError),
}

pub struct IsoTpSession {
    pub request_id: u32,       // Tester → ECU (e.g. 0x7E0)
    pub response_id: u32,      // ECU → Tester (e.g. 0x7E8)
    pub state: IsoTpSessionState,
    pub is_extended_id: bool,
    pub padding_byte: Option<u8>,  // 0xCC for most OEMs
}
```

### 2.3 Standart CAN ID Çiftleri

| Kullanım | Tester TX | ECU RX | ECU TX | Tester RX |
|----------|-----------|--------|--------|-----------|
| OBD-II (11-bit, broadcast) | `0x7DF` | `0x7Dx` | `0x7Ex` | `0x7E8`..`0x7EF` |
| OBD-II (11-bit, physical) | `0x7E0` | `0x7E0` | `0x7E8` | `0x7E8` |
| OBD-II (29-bit J1939) | `0x18DB33F1` | `0x18DAxxF1` | `0x18DAF1xx` | — |
| UDS (11-bit) | `0x7E0`..`0x7E7` | — | `0x7E8`..`0x7EF` | — |

> **Not:** Kullanıcı CAN ID'leri `DiagnosticsView`'da özelleştirebilir (bazı OEM'ler standart dışı ID kullanır).

### 2.4 Flow Control Parametreleri

```rust
pub struct FlowControlParams {
    pub block_size: u8,      // 0 = tüm frame'leri gönder; 1..255 = N frame gönder, sonra FC bekle
    pub st_min_us: u32,      // 0x00 = 0µs; 0x01..0x7F = 1..127ms; 0xF1..0xF9 = 100µs..900µs
}
```

---

## 3. OBD-II Servis Katmanı (`obd2/`)

### 3.1 Desteklenecek Modlar

| Mode | Servis Adı | Açıklama |
|------|-----------|----------|
| `0x01` | Show Current Data | Canlı PID okuma (RPM, hız, sıcaklıklar…) |
| `0x02` | Show Freeze Frame | DTC tetiklendiğindeki anlık görüntü verileri |
| `0x03` | Show Stored DTCs | Kalıcı arıza kodları (P/C/B/U) |
| `0x04` | Clear DTCs | Tüm DTC ve freeze frame'leri sil |
| `0x07` | Show Pending DTCs | Test sürüşündeki bekleyen kodlar |
| `0x09` | Vehicle Information | VIN (PID 0x02), ECU adı (0x0A), Cal ID (0x04) |

### 3.2 Mode 01 — Standart PID Tablosu (Öncelikli Subset)

```rust
pub struct PidDefinition {
    pub pid: u8,
    pub name: &'static str,
    pub bytes: u8,            // Response byte sayısı (A, B, C, D)
    pub formula: PidFormula,  // Raw → fiziksel değer dönüşümü
    pub unit: &'static str,
    pub min: f64,
    pub max: f64,
}

pub enum PidFormula {
    Linear { a: f64, b: f64 },   // physical = A * raw + B
    Custom(fn([u8; 4]) -> f64),  // Özel formüller
}
```

| PID | Ad | Formül | Birim | Aralık |
|-----|----|--------|-------|--------|
| `0x04` | Engine Load | A/2.55 | % | 0–100 |
| `0x05` | Coolant Temp | A-40 | °C | -40–215 |
| `0x0C` | Engine RPM | (256A+B)/4 | rpm | 0–16383.75 |
| `0x0D` | Vehicle Speed | A | km/h | 0–255 |
| `0x0F` | Intake Air Temp | A-40 | °C | -40–215 |
| `0x10` | MAF Air Flow | (256A+B)/100 | g/s | 0–655.35 |
| `0x11` | Throttle Pos | A/2.55 | % | 0–100 |
| `0x1C` | OBD Standard | Enum | — | — |
| `0x2F` | Fuel Tank Level | A/2.55 | % | 0–100 |
| `0x33` | Barometric Press | A | kPa | 0–255 |
| `0x42` | Control Module Voltage | (256A+B)/1000 | V | 0–65.535 |
| `0x46` | Ambient Air Temp | A-40 | °C | -40–215 |
| `0x5C` | Engine Oil Temp | A-40 | °C | -40–215 |
| `0x61` | Driver Demand Torque | A-125 | % | -125–130 |
| `0x62` | Actual Engine Torque | A-125 | % | -125–130 |

### 3.3 DTC Format Decode

```
Byte[0] bits [7:6] → Kategori
  00 → P (Powertrain)   01 → C (Chassis)
  10 → B (Body)         11 → U (Network)

Byte[0] bits [5:4] → 1. digit (0–3)
Byte[0] bits [3:0] → 2. digit (hex)
Byte[1] bits [7:4] → 3. digit (hex)
Byte[1] bits [3:0] → 4. digit (hex)

Örn: 0x01 0x00 → P0100 (MAF Sensor Circuit Malfunction)
```

---

## 4. UDS Servis Katmanı (`uds/`)

### 4.1 Desteklenecek Servisler

| SID | Servis Adı | Öncelik | Açıklama |
|-----|-----------|---------|----------|
| `0x10` | DiagnosticSessionControl | P1 | Default / Extended / Programming session |
| `0x11` | ECUReset | P2 | Hard / Soft / enableRapidPowerShutdown |
| `0x14` | ClearDiagnosticInformation | P1 | DTC grubu temizleme (`0xFFFFFF` = tümü) |
| `0x19` | ReadDTCInformation | P1 | 10+ sub-function; status mask & snapshot |
| `0x22` | ReadDataByIdentifier | P1 | DID okuma (VIN, ECU S/W versiyonu, aktif session…) |
| `0x27` | SecurityAccess | P2 | Seed/Key doğrulama (sadece UI; anahtar hesabı kullanıcıya bırakılır) |
| `0x2E` | WriteDataByIdentifier | P3 | DID yazma (sadece gelişmiş kullanıcılar) |
| `0x3E` | TesterPresent | P1 | Session canlı tutma (arka planda otomatik gönderim) |
| `0x85` | ControlDTCSetting | P2 | DTC kayıt etkinleştir / devre dışı bırak |

### 4.2 Session Yönetimi

```rust
pub enum UdsSession {
    Default,            // 0x01 — Her zaman erişilebilir, kısıtlı servisler
    Programming,        // 0x02 — ECU flash/download (dikkatli kullanım)
    Extended,           // 0x03 — Teşhis & kalibrasyon servisleri
    Custom(u8),
}
```

**TesterPresent Otomasyonu:** Extended session açıkken `0x3E 0x80` (suppress positive response) her 2 saniyede otomatik gönderilir; kullanıcı bağlantıyı kesmeden session timeout olmaz.

### 4.3 ReadDTCInformation (0x19) Sub-Fonksiyonları

| Sub-Fn | İsim | Açıklama |
|--------|------|----------|
| `0x01` | reportNumberOfDTCByStatusMask | Belirli status mask'e uyan DTC sayısı |
| `0x02` | reportDTCByStatusMask | Status mask'e uyan DTC listesi (her biri 3 byte + status) |
| `0x06` | reportDTCExtDataRecordByDTCNumber | Belirli bir DTC için genişletilmiş veri kaydı |
| `0x0A` | reportSupportedDTC | ECU'nun desteklediği tüm DTC'ler |

**DTC Status Byte Bitleri:**
```
Bit 0: testFailed               — Mevcut sürüş döngüsünde arıza
Bit 1: testFailedThisOperationCycle
Bit 2: pendingDTC               — Sadece bir sürüş döngüsünde görüldü
Bit 3: confirmedDTC             — Kalıcı DTC (en az 2 sürüş döngüsü)
Bit 4: testNotCompletedSinceLastClear
Bit 5: testFailedSinceLastClear
Bit 6: testNotCompletedThisOperationCycle
Bit 7: warningIndicatorRequested — MIL (Malfunction Indicator Lamp) yandı mı
```

### 4.4 Standart DID Tablosu (0x22)

| DID | İsim | Açıklama |
|-----|------|----------|
| `0xF186` | ActiveDiagnosticSession | Mevcut UDS session türü |
| `0xF187` | VehicleManufacturerSparePartNumber | Yedek parça numarası |
| `0xF188` | VehicleManufacturerECUSoftwareVersionNumber | ECU yazılım versiyonu |
| `0xF189` | VehicleManufacturerECUSoftwareVersionNumber | — |
| `0xF18A` | SystemSupplierIdentifier | Tedarikçi kodu |
| `0xF18B` | ECUManufacturingDate | Üretim tarihi (YY/MM/DD) |
| `0xF18C` | ECUSerialNumber | ECU seri numarası |
| `0xF190` | VIN | 17 karakterli araç kimlik numarası |
| `0xF191` | VehicleManufacturerECUHardwareNumber | Donanım parça numarası |
| `0xF192`..`0xF194` | SystemSupplierECUHardware/SoftwareVersionNumber | Tedarikçi versiyon |
| `0xF197` | VehicleManufacturerECUBootSoftwareVersionNumber | Boot yazılım versiyonu |

### 4.5 Negative Response Kodları (NRC)

| NRC | İsim | Kullanıcıya Gösterme |
|-----|------|---------------------|
| `0x10` | generalReject | "ECU isteği reddetti" |
| `0x11` | serviceNotSupported | "Bu servis desteklenmiyor" |
| `0x12` | subFunctionNotSupported | "Alt fonksiyon desteklenmiyor" |
| `0x13` | incorrectMessageLengthOrInvalidFormat | "Hatalı mesaj uzunluğu" |
| `0x22` | conditionsNotCorrect | "Koşullar sağlanmıyor (session/security?)" |
| `0x24` | requestSequenceError | "Yanlış sıralama (SecurityAccess seed önce)" |
| `0x25` | noResponseFromSubnetComponent | "Alt ağ bileşeni yanıt vermiyor" |
| `0x31` | requestOutOfRange | "Geçersiz DID/PID" |
| `0x33` | securityAccessDenied | "Güvenlik erişimi reddedildi" |
| `0x35` | invalidKey | "Hatalı güvenlik anahtarı" |
| `0x36` | exceededNumberOfAttempts | "Çok fazla başarısız deneme, ECU kilitli" |
| `0x37` | requiredTimeDelayNotExpired | "Bekleme süresi dolmadı" |
| `0x78` | requestCorrectlyReceived–ResponsePending | "ECU işliyor (uzun sürebilir)" |

---

## 5. Veri Transfer Objeleri (`dto.rs`)

```rust
// Tauri IPC üzerinden Frontend'e gönderilir
#[derive(Serialize, Deserialize)]
pub struct DiagSessionInfoDto {
    pub request_id: u32,
    pub response_id: u32,
    pub is_extended_id: bool,
    pub current_uds_session: String,    // "default" | "extended" | "programming"
    pub tester_present_active: bool,
    pub last_response_ms: Option<u64>,
}

#[derive(Serialize, Deserialize)]
pub struct DiagResponseDto {
    pub service_id: u8,
    pub success: bool,
    pub raw_payload: Vec<u8>,
    pub error_code: Option<u8>,         // NRC kodu
    pub error_description: Option<String>,
    pub decoded: Option<serde_json::Value>, // Servis tipine göre decode edilmiş veri
    pub latency_ms: u64,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct DtcEntryDto {
    pub code: String,                   // "P0100", "C0045" vb.
    pub raw: u32,                       // 3-byte DTC ham değeri
    pub status_byte: u8,
    pub is_confirmed: bool,
    pub is_pending: bool,
    pub is_mil_on: bool,
    pub description: Option<String>,    // İleride DTC veritabanından
}

#[derive(Serialize, Deserialize, Clone)]
pub struct LivePidDto {
    pub pid: u8,
    pub name: &'static str,
    pub raw_value: u32,
    pub physical_value: f64,
    pub unit: String,
    pub timestamp_ms: u64,
}
```

---

## 6. Tauri IPC Komutları

`src-tauri/src/commands.rs` dosyasına eklenecek yeni komutlar:

```rust
// --- Diagnostics Commands ---

/// Diagnostics session başlat (ISO-TP yapılandırması)
#[tauri::command]
pub async fn cmd_diag_open_session(
    request_id: u32,
    response_id: u32,
    is_extended: bool,
    bus_id: u8,
) -> Result<DiagSessionInfoDto, String>

/// UDS DiagnosticSessionControl (0x10)
#[tauri::command]
pub async fn cmd_diag_session_control(
    session_type: u8,  // 0x01 default, 0x02 programming, 0x03 extended
) -> Result<DiagResponseDto, String>

/// OBD-II veya UDS tek servis isteği gönder
#[tauri::command]
pub async fn cmd_diag_send_request(
    service_id: u8,
    sub_function: Option<u8>,
    data: Vec<u8>,
) -> Result<DiagResponseDto, String>

/// Mode 01 — Canlı PID listesi oku (batch)
#[tauri::command]
pub async fn cmd_diag_read_live_pids(
    pids: Vec<u8>,
) -> Result<Vec<LivePidDto>, String>

/// Mode 03 — Saklanan DTC'leri oku
#[tauri::command]
pub async fn cmd_diag_read_stored_dtcs() -> Result<Vec<DtcEntryDto>, String>

/// Mode 07 — Bekleyen DTC'leri oku
#[tauri::command]
pub async fn cmd_diag_read_pending_dtcs() -> Result<Vec<DtcEntryDto>, String>

/// Mode 04 / UDS 0x14 — DTC'leri temizle
#[tauri::command]
pub async fn cmd_diag_clear_dtcs(
    group: u32,  // 0xFFFFFF = tümü
) -> Result<DiagResponseDto, String>

/// UDS 0x22 — DID oku
#[tauri::command]
pub async fn cmd_diag_read_did(
    did: u16,
) -> Result<DiagResponseDto, String>

/// UDS 0x27 — SecurityAccess seed iste
#[tauri::command]
pub async fn cmd_diag_security_access_request_seed(
    access_level: u8,
) -> Result<DiagResponseDto, String>

/// UDS 0x27 — SecurityAccess key gönder
#[tauri::command]
pub async fn cmd_diag_security_access_send_key(
    access_level: u8,
    key: Vec<u8>,
) -> Result<DiagResponseDto, String>

/// Session kapat, TesterPresent timer'ı durdur
#[tauri::command]
pub async fn cmd_diag_close_session() -> Result<(), String>
```

---

## 7. Frontend Bileşenleri (`src/`)

### 7.1 Yeni Dosyalar

```
src/
├── components/
│   └── diagnostics/
│       ├── DiagnosticsView.tsx          # Ana view — toolpack guard ile sarılı
│       ├── DiagSessionSetup.tsx         # CAN ID, ECU adres, session tipi yapılandırması
│       ├── DtcPanel.tsx                 # DTC liste tablosu (Stored + Pending, Clear butonu)
│       ├── LivePidGauges.tsx            # Seçilebilir PID ızgarası — mini gauge'lar
│       ├── UdsConsole.tsx               # Manuel UDS servis konsolu (hex girişi)
│       ├── DidInspector.tsx             # DID okuma paneli (VIN, SW version vb.)
│       ├── IsoTpMonitor.tsx             # Ham ISO-TP çerçeve akışı (debug modu)
│       └── DiagHistoryLog.tsx           # Zaman damgalı istek/yanıt geçmişi
├── store/
│   └── diagnosticsStore.ts              # Zustand store — session, DTCs, live PIDs
├── types/
│   └── diagnostics.ts                   # DiagSessionInfoDto, DtcEntryDto, LivePidDto
└── i18n/
    └── shards/
        └── diagnostics.ts               # TR/EN çeviriler
```

### 7.2 DiagnosticsView — Ana Görünüm

```
┌─────────────────────────────────────────────────────────────────┐
│ [UDS/OBD-II]  ECU: 0x7E0 → 0x7E8  Session: DEFAULT  [Bağlan]   │
│               Bus: vcan0            TesterPresent: ●  [Kapat]    │
├─────────────────────────────────────────────────────────────────┤
│  [DTC Tara]  [DTC Temizle]  [PID İzle]  [DID Oku]  [UDS Konsol] │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ARIZA KODLARI (DTC)                   ← tab seçimine göre      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Kod    │ Durum        │ MIL │ Açıklama                   │    │
│  │ P0100  │ Confirmed ✓  │ ●   │ MAF Sensor Circuit         │    │
│  │ P0300  │ Pending  ◐   │ ○   │ Random/Multiple Misfire    │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                   │
│  CANLÜ PID İZLEME                                                │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐   │
│  │ RPM        │ │ Hız        │ │ Su Sıcak.  │ │ Throttle   │   │
│  │  2340      │ │  87 km/h   │ │  91 °C     │ │  34%       │   │
│  │  ════════  │ │  ═══════○  │ │  ════════  │ │  ═══○      │   │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 7.3 diagnosticsStore.ts Yapısı

```typescript
interface DiagnosticsState {
  session: DiagSessionInfoDto | null;
  storedDtcs: DtcEntryDto[];
  pendingDtcs: DtcEntryDto[];
  livePids: Map<number, LivePidDto>;
  activePidPolling: number[];        // İzlenen PID'ler
  pidPollingIntervalMs: number;      // Default: 500ms
  requestHistory: DiagHistoryEntry[];
  isLoading: boolean;
  lastError: string | null;
}
```

### 7.4 Toolpack Guard

`DiagnosticsView` sadece `automotive_diagnostics` toolpack etkinleştirildiğinde görünür:

```tsx
// App.tsx veya ilgili route içinde:
{isToolpackEnabled(enabledToolpacks, 'automotive_diagnostics') && (
  <DiagnosticsView />
)}
```

---

## 8. I18n Eklentileri

`src/i18n/shards/diagnostics.ts` dosyasına eklenecek anahtar grupları:

```typescript
// diagnostics.session.*    — Session yönetimi metinleri
// diagnostics.dtc.*        — DTC panel metinleri
// diagnostics.pid.*        — PID izleme metinleri
// diagnostics.uds.*        — UDS konsol metinleri
// diagnostics.did.*        — DID okuma metinleri
// diagnostics.isotp.*      — ISO-TP monitor metinleri
// diagnostics.error.*      — Hata ve NRC açıklamaları
```

---

## 9. Cargo.toml Değişiklikleri

### `Cargo.toml` (Workspace root)

```toml
[workspace]
members = [
    "crates/nexus-core",
    "crates/nexus-hal",
    "crates/nexus-netdb",
    "crates/nexus-diag",    # ← YENİ
    "crates/nexus-cloud",
    "crates/nexus-cli",
    "src-tauri",
]
```

### `crates/nexus-diag/Cargo.toml`

```toml
[package]
name = "nexus-diag"
version = "0.1.0"
edition = "2021"

[dependencies]
nexus-core = { path = "../nexus-core" }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
thiserror = "2.0"
tracing = "0.1"
tokio = { version = "1", features = ["sync", "time"] }

[dev-dependencies]
tokio = { version = "1", features = ["rt", "macros"] }
```

### `src-tauri/Cargo.toml`

```toml
[dependencies]
# ... mevcut bağımlılıklar ...
nexus-diag = { path = "../crates/nexus-diag" }  # ← YENİ
```

---

## 10. Uygulama Fazları

### Faz 3-A: Temel Altyapı (nexus-diag crate) — ~2 hafta

- [ ] `crates/nexus-diag/` crate iskeletini oluştur
- [ ] `isotp/frame.rs` — SF/FF/CF/FC parser/builder (birim testleri dahil)
- [ ] `isotp/session.rs` — TX assembly ve RX defragmentation state machine
- [ ] `obd2/dtc.rs` — DTC byte decode, P/C/B/U kategori ayrımı
- [ ] `obd2/pids.rs` — Mode 01 PID kataloğu ve formül tablosu
- [ ] `uds/nrc.rs` — 0x7F negative response decoder
- [ ] `dto.rs` — Tüm IPC DTO yapıları
- [ ] Kapsamlı birim testleri (`cargo test -p nexus-diag`)

### Faz 3-B: Tauri Entegrasyonu — ~1 hafta

- [ ] `DiagEngine` singleton — `Arc<Mutex<DiagEngine>>` Tauri state olarak kayıt
- [ ] ISO-TP RX filtresi: `StateTable` pipeline'ına sadece diag CAN ID'lerini takip eden dinleyici
- [ ] Tüm `cmd_diag_*` Tauri komutlarını implement et
- [ ] TesterPresent arka plan timer (Tokio task)
- [ ] Timeout ve NRC hata yönetimi

### Faz 3-C: Frontend — ~2 hafta

- [ ] `src/types/diagnostics.ts` oluştur
- [ ] `diagnosticsStore.ts` Zustand store
- [ ] `DiagSessionSetup.tsx` — Bağlantı yapılandırması
- [ ] `DtcPanel.tsx` — DTC listesi, renk kodlu status badge'leri
- [ ] `LivePidGauges.tsx` — Seçilebilir PID grid'i, 500ms polling
- [ ] `UdsConsole.tsx` — Serbest servis istekleri
- [ ] `DidInspector.tsx` — Standart DID'leri hızlı okuma
- [ ] `DiagnosticsView.tsx` — Tab tabanlı ana görünüm
- [ ] I18n shard (`diagnostics.ts`) TR + EN

### Faz 3-D: Test & Doğrulama — ~1 hafta

- [ ] `vcan0` üzerinde Python `python-can` / `udsoncan` kütüphanesi ile mock ECU
- [ ] ISO-TP multi-frame defragmentation (>7 byte) end-to-end testi
- [ ] OBD-II Mode 03 DTC decode doğrulaması
- [ ] UDS 0x22 (VIN okuma) ve 0x19 (DTC listesi) testi
- [ ] TesterPresent timeout senaryosu testi
- [ ] NRC hata yönetimi (0x78 ResponsePending döngüsü) testi

---

## 11. Güvenlik ve Dikkat Noktaları

1. **Mode 0x02 (Programming Session):** Bu session, ECU flashing için açılır. UI'da büyük sarı uyarı banner'ı gösterilmeli; kullanıcı açık rıza vermeden bu session başlatılmamalı.

2. **DTC Silme (Mode 04 / 0x14):** Geri alınamaz işlem. Confirm dialog zorunlu.

3. **SecurityAccess (0x27):** NexusCAN anahtar hesabı yapmaz. Kullanıcı seed'i alır, kendi hesaplayıp key girer. Brute force koruması: ardışık 3 başarısız denemede ECU kilitlenebilir, UI bunu `0x36` NRC ile net uyarır.

4. **WriteDataByIdentifier (0x2E):** Varsayılan olarak devre dışı. `nexusproj.json`'da `allow_did_write: true` ile açılabilir.

5. **Araç Üzerinde Kullanım:** Araç hareket halindeyken diagnostics başlatmak güvenlik riski oluşturabilir. İleride `vehicle_speed` PID kontrolü ile hareket algılama eklenebilir.

---

## 12. İleride Genişletilebilir Alanlar

| Alan | Açıklama |
|------|----------|
| **DTC Veritabanı** | P0xxx..P3xxx kod açıklamalarını içeren yerel SQLite veritabanı |
| **Freeze Frame Explorer** | Mode 02 ile DTC tetiklenme anındaki tüm PID anlık görüntüsü |
| **UDS Download/Upload (0x34/0x35)** | ECU binary flash (sadece güvenli, yetkilendirilmiş kullanım) |
| **DoIP (ISO 13400)** | Ethernet üzerinden diagnostics (EV/Ethernet ECU'lar için) |
| **CAN-FD ISO-TP** | 64-byte CAN-FD frame'leriyle daha hızlı ISO-TP aktarımı |
| **Kalibrasyon Parametreleri (0x2C/0x2F)** | DynamicallyDefineDataIdentifier ve InputOutputControl |
| **Routine Control (0x31)** | ECU self-test ve kalibrasyon rutinleri |

---

*Son güncelleme: 2026-11-09 | Durum: Planlama*
