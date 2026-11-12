#include <SPI.h>
#include <MFRC522.h>
#include "liliya_net.h"

// Wiring RC522 -> ESP32-C3
#define PIN_SCK   4
#define PIN_MISO  5
#define PIN_MOSI  6
#define PIN_SS    7   // SDA di modul
#define PIN_RST   3
#define LED       8   // LED bawaan (aktif LOW)
#define PIN_SERVO 10  // sinyal servo MG946R (daya dari charger 5V terpisah)
#define BUZZER    1   // buzzer aktif (+ ke GPIO 1, - ke GND)
#define PIN_IR    0   // OUT sensor IR (VCC ke 3V3): LOW = ada benda = pintu tertutup

// Sudut relatif terhadap titik tengah servo (0 = 1500us):
// "-" = arah buka, "+" = arah tutup
#define ANGLE_LOCK   30    // posisi terkunci (slot menutup), LED mati
#define ANGLE_OPEN   90    // posisi terbuka (kunci + 60 derajat), LED menyala
#define STEP_MS      10    // servo bergerak 1 derajat tiap 10 ms (60 derajat = 0,6 detik)
#define HOLD_MS      800   // tahan posisi setelah sampai, setelah itu PWM dilepas
#define SERVO_MIN_US 500   // pulsa untuk 0 derajat
#define SERVO_MAX_US 2500  // pulsa untuk 180 derajat
#define SERVO_BITS   14

// Semua kartu di daftar known[] hanya bisa membuka kunci; mengunci dilakukan
// otomatis lewat sensor IR (atau dari aplikasi)

#define AUTOLOCK_MS      3000    // kunci otomatis setelah pintu tertutup selama ini
#define REMIND_AFTER_MS  60000   // pintu terbuka lebih lama dari ini -> notifikasi HP

MFRC522 rfid(PIN_SS, PIN_RST);
bool isOpen = false;
int currentAngle = ANGLE_LOCK;   // sudut tujuan
int servoPos = ANGLE_LOCK;       // sudut yang sedang dikirim ke servo
bool servoActive = false;
unsigned long nextStepAt = 0;
bool doorClosed = false;
unsigned long irChangedAt = 0;
bool doorOpenedSinceUnlock = false;   // kunci otomatis hanya setelah pintu benar-benar dibuka
unsigned long doorChangedAt = 0, lastReminder = 0;

// Kartu yang dikenal: tempel UID dari Serial Monitor ke sini
struct Card { const char *uid; const char *name; };
Card known[] = { KNOWN_CARDS_INIT };   // isi di secrets.h (tidak ikut di-commit)
const int KNOWN_COUNT = sizeof(known) / sizeof(known[0]);

String uidToString(const MFRC522::Uid &u) {
  String s;
  for (byte i = 0; i < u.size; i++) {
    if (u.uidByte[i] < 0x10) s += "0";
    s += String(u.uidByte[i], HEX);
    if (i < u.size - 1) s += " ";
  }
  s.toUpperCase();
  return s;
}

const char *lookup(const String &uid) {
  for (int i = 0; i < KNOWN_COUNT; i++)
    if (uid == known[i].uid) return known[i].name;
  return nullptr;
}

// Pakai LEDC langsung: ESP32Servo selalu mengirim pulsa 90 derajat saat attach,
// membuat servo menyentak ke tengah sebelum ke sudut tujuan.
void writeServo(int angle) {
  uint32_t us = map(-angle, -90, 90, SERVO_MIN_US, SERVO_MAX_US);
  ledcWrite(PIN_SERVO, us * ((1 << SERVO_BITS) - 1) / 20000);
}

// Servo digerakkan bertahap (bukan loncat langsung) supaya lonjakan arus kecil
// dan tegangan baterai/step-up tidak anjlok; setelah sampai ditahan HOLD_MS
// lalu PWM dilepas supaya servo tidak terus menarik arus
void moveServo(int angle) {
  currentAngle = angle;
  if (servoPos == angle) { writeServo(angle); nextStepAt = millis() + HOLD_MS; }
  else nextStepAt = millis();
  servoActive = true;
}

void serviceServo() {
  if (!servoActive || (long)(millis() - nextStepAt) < 0) return;
  if (servoPos == currentAngle) { ledcWrite(PIN_SERVO, 0); servoActive = false; return; }
  servoPos += servoPos < currentAngle ? 1 : -1;
  writeServo(servoPos);
  nextStepAt = millis() + (servoPos == currentAngle ? HOLD_MS : STEP_MS);
}

// delay() yang tetap menjalankan gerakan servo
void waitMs(unsigned long ms) {
  unsigned long start = millis();
  while (millis() - start < ms) { serviceServo(); delay(1); }
}

// Buzzer mengikuti servo: bunyi terus selama kunci terbuka, mati setelah
// kunci otomatis menutup
void updateBuzzer() {
  digitalWrite(BUZZER, isOpen ? HIGH : LOW);
}

// Bunyikan buzzer sekaligus kedipkan LED
void beep(int times, int ms) {
  for (int i = 0; i < times; i++) {
    digitalWrite(BUZZER, HIGH); digitalWrite(LED, LOW);  waitMs(ms);
    digitalWrite(BUZZER, LOW);  digitalWrite(LED, HIGH); waitMs(ms);
  }
  digitalWrite(LED, isOpen ? LOW : HIGH);
  updateBuzzer();
}

// Kalibrasi via Serial: "+3" = 3 derajat ke arah tutup, "-3" = ke arah buka,
// "=-30" = langsung ke -30 derajat, "?" = tampilkan posisi tanpa bergerak
void handleSerial() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.length() == 0) return;
  if (cmd == "?") {
    Serial.printf("POSISI: %d derajat, status %s, pintu %s\n", currentAngle,
                  isOpen ? "TERBUKA" : "TERKUNCI", doorClosed ? "TERTUTUP" : "TERBUKA");
    if (WiFi.status() == WL_CONNECTED)
      Serial.printf("WIFI: terhubung %s, IP %s, sinyal %d dBm\n", WIFI_SSID, WiFi.localIP().toString().c_str(), WiFi.RSSI());
    else
      Serial.printf("WIFI: belum terhubung ke %s (status %d)\n", WIFI_SSID, WiFi.status());
    return;
  }
  int angle = (cmd[0] == '=') ? cmd.substring(1).toInt() : currentAngle + cmd.toInt();
  angle = constrain(angle, -90, 90);
  moveServo(angle);
  Serial.printf("KALIBRASI: servo ke %d derajat\n", angle);
}

// Baca sensor IR dengan debounce 150ms supaya pantulan yang berkedip di batas
// jangkauan tidak membuat status pintu bolak-balik
void serviceDoor() {
  bool closedNow = digitalRead(PIN_IR) == LOW;
  if (closedNow == doorClosed) { irChangedAt = 0; return; }
  if (!irChangedAt) { irChangedAt = millis(); return; }
  if (millis() - irChangedAt < 150) return;
  doorClosed = closedNow;
  irChangedAt = 0;
  doorChangedAt = millis();
  lastReminder = 0;
  if (isOpen && !doorClosed) doorOpenedSinceUnlock = true;
  Serial.printf("PINTU: %s\n", doorClosed ? "TERTUTUP" : "TERBUKA");
  netEvent(doorClosed ? "door_closed" : "door_open", !isOpen, doorClosed);
}

void unlock(const char *by, const char *cmdId = "") {
  isOpen = true;
  // Jika pintu sudah terbuka saat kunci dibuka, transisi tutup->buka tidak
  // akan pernah terlihat; anggap sudah dibuka agar kunci otomatis tetap jalan
  doorOpenedSinceUnlock = !doorClosed;
  moveServo(ANGLE_OPEN);
  beep(1, 150);             // buka: bip pendek sekali
  Serial.printf("TERBUKA (%s)\n", by);
  netEvent("unlock", false, doorClosed, by, "", cmdId);
}

void lock(const char *by, const char *cmdId = "") {
  isOpen = false;
  doorOpenedSinceUnlock = false;
  moveServo(ANGLE_LOCK);
  beep(2, 80);
  Serial.printf("TERKUNCI (%s)\n", by);
  netEvent("lock", true, doorClosed, by, "", cmdId);
}

// Slot kunci jangan menutup saat pintu masih terbuka
void rejectLock(const char *by, const char *cmdId = "") {
  beep(1, 700);
  Serial.printf("DITOLAK (%s): pintu masih terbuka\n", by);
  netEvent("lock_rejected", false, doorClosed, by, "", cmdId);
}

// Perintah dari aplikasi Liliya / suara Budi
void handleCommands() {
  NetCmd c;
  while (netNextCmd(c)) {
    if (!strcmp(c.cmd, "unlock")) {
      if (isOpen) netEvent("unlock", false, doorClosed, c.by, "", c.id);   // sudah terbuka
      else unlock(c.by, c.id);
    } else if (!strcmp(c.cmd, "lock")) {
      if (!isOpen) netEvent("lock", true, doorClosed, c.by, "", c.id);     // sudah terkunci
      else if (!doorClosed) rejectLock(c.by, c.id);
      else lock(c.by, c.id);
    }
  }
}

// Kunci otomatis setelah pintu ditutup, dan notifikasi jika pintu lama terbuka
// (buzzer sendiri sudah berbunyi terus selama kunci terbuka)
void serviceDoorLogic() {
  unsigned long now = millis();
  if (isOpen && doorOpenedSinceUnlock && doorClosed && now - doorChangedAt >= AUTOLOCK_MS)
    lock("otomatis");

  if (isOpen && !doorClosed && !lastReminder && now - doorChangedAt >= REMIND_AFTER_MS) {
    netEvent("reminder", !isOpen, doorClosed);   // laporkan sekali per kejadian
    lastReminder = now;
    Serial.printf("PERINGATAN: pintu terbuka %lu detik\n", (now - doorChangedAt) / 1000);
  }
}

// Tegangan yang anjlok saat servo bergerak bisa membuat RC522 macet walau
// ESP32 tetap hidup; cek berkala dan inisialisasi ulang bila perlu
#define RFID_CHECK_MS  5000
#define RFID_REINIT_MS 60000
void serviceRfid() {
  static unsigned long lastCheck = 0, lastInit = 0;
  unsigned long now = millis();
  if (now - lastCheck < RFID_CHECK_MS) return;
  lastCheck = now;
  byte v = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  bool bad = v == 0x00 || v == 0xFF;
  // Register versi bisa tetap terbaca normal padahal antena sudah mati
  bool antennaOff = (rfid.PCD_ReadRegister(MFRC522::TxControlReg) & 0x03) != 0x03;
  if (bad || antennaOff || now - lastInit >= RFID_REINIT_MS) {
    if (bad || antennaOff) Serial.printf("RC522 tidak merespon (0x%02X), inisialisasi ulang\n", v);
    rfid.PCD_Init();
    lastInit = now;
  }
}

// Kartu yang ditahan menempel akan terbaca berulang; tunggu sampai kartu
// diangkat (3x berturut-turut tidak menjawab) supaya satu tap = satu aksi
void waitCardRemoved() {
  int absent = 0;
  while (absent < 3) {
    byte atqa[2], len = sizeof(atqa);
    if (rfid.PICC_WakeupA(atqa, &len) == MFRC522::STATUS_OK) {
      absent = 0;
      rfid.PICC_HaltA();
    } else {
      absent++;
    }
    serviceDoor();
    serviceDoorLogic();
    waitMs(100);
  }
}

void setup() {
  Serial.begin(115200);
  // Tanpa terminal yang membaca, print di USB-CDC menunggu ~100ms per baris
  // dan membuat respon tap terasa lambat; buang saja output jika tidak dibaca
  Serial.setTxTimeoutMs(0);
  pinMode(LED, OUTPUT);
  digitalWrite(LED, HIGH);
  pinMode(BUZZER, OUTPUT);
  digitalWrite(BUZZER, LOW);
  pinMode(PIN_IR, INPUT_PULLUP);
  doorClosed = digitalRead(PIN_IR) == LOW;
  delay(1500);

  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_SS);
  rfid.PCD_Init();
  delay(50);

  ledcAttach(PIN_SERVO, 50, SERVO_BITS);
  ledcWrite(PIN_SERVO, 0);   // belum ada pulsa sampai sudut pertama ditulis
  moveServo(ANGLE_LOCK);

  byte v = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  Serial.printf("\nRC522 firmware: 0x%02X ", v);
  if (v == 0x00 || v == 0xFF) Serial.println("-> TIDAK TERDETEKSI, cek kabel!");
  else Serial.println("-> OK, tempelkan kartu...");
  Serial.println("Status: TERKUNCI");
  Serial.printf("PINTU: %s\n", doorClosed ? "TERTUTUP" : "TERBUKA");

  netBegin();
  // Alasan menyala ulang ikut dikirim supaya terlihat di riwayat aplikasi
  // apakah karena tegangan anjlok (brownout) atau program crash
  const char *why;
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:  why = "daya dinyalakan"; break;
    case ESP_RST_BROWNOUT: why = "tegangan anjlok"; break;
    case ESP_RST_PANIC:    why = "program crash"; break;
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:      why = "watchdog"; break;
    case ESP_RST_SW:       why = "restart program"; break;
    case ESP_RST_USB:      why = "reset USB"; break;
    default:               why = "lainnya"; break;
  }
  Serial.printf("Alasan menyala: %s\n", why);
  netEvent("boot", true, doorClosed, why);
}

void loop() {
  serviceServo();
  serviceDoor();
  serviceDoorLogic();
  handleSerial();
  handleCommands();
  serviceRfid();

  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    waitMs(50);
    return;
  }

  String uid = uidToString(rfid.uid);
  MFRC522::PICC_Type type = rfid.PICC_GetType(rfid.uid.sak);
  const char *name = lookup(uid);

  // Aksi dulu (servo + buzzer), baru log ke Serial
  bool wasOpen = isOpen;
  if (name) {
    if (!isOpen) unlock(name);
    else beep(1, 150);          // sudah terbuka: cukup bip, kunci tetap lewat IR
  } else {
    beep(3, 100);               // asing: bip-bip-bip cepat
    netEvent("unknown_card", !isOpen, doorClosed, "", uid.c_str());
  }

  Serial.println("--------------------------------");
  Serial.printf("UID  : %s\n", uid.c_str());
  Serial.print("Tipe : ");
  Serial.println(rfid.PICC_GetTypeName(type));
  if (name)
    Serial.printf("DIKENAL: %s -> %s\n", name, wasOpen ? "sudah terbuka" : "TERBUKA");
  else
    Serial.println("TIDAK DIKENAL");

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  waitCardRemoved();
}
