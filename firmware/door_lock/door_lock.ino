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
#define ANGLE_LOCK   20    // posisi terkunci (slot menutup), LED mati
#define ANGLE_OPEN   80    // posisi terbuka (kunci + 60 derajat), LED menyala
#define MOVE_MS      700   // waktu servo bergerak, setelah itu PWM dilepas
#define SERVO_MIN_US 500   // pulsa untuk 0 derajat
#define SERVO_MAX_US 2500  // pulsa untuk 180 derajat
#define SERVO_BITS   14

// Satu-satunya kartu yang boleh membuka/mengunci servo
#define OWNER_UID "E8 1B 53 77"   // Fazri (kartu putih)

#define AUTOLOCK_MS      3000    // kunci otomatis setelah pintu tertutup selama ini
#define REMIND_AFTER_MS  60000   // pintu terbuka lebih lama dari ini -> pengingat
#define REMIND_EVERY_MS  10000   // jarak antar bunyi pengingat

MFRC522 rfid(PIN_SS, PIN_RST);
unsigned long detachAt = 0;
bool isOpen = false;
int currentAngle = ANGLE_LOCK;
bool doorClosed = false;
unsigned long irChangedAt = 0;
bool doorOpenedSinceUnlock = false;   // kunci otomatis hanya setelah pintu benar-benar dibuka
unsigned long doorChangedAt = 0, lastReminder = 0;

// Kartu yang dikenal: tempel UID dari Serial Monitor ke sini
struct Card { const char *uid; const char *name; };
Card known[] = {
  {"E8 1B 53 77", "Fazri"},  // kartu putih
  {"D1 A5 A4 66", "Lili"},   // gantungan kunci
  {"FF BD A0 02", "Liya"},   // kartu Jakarta Tourist
};
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

// Bunyikan buzzer sekaligus kedipkan LED
void beep(int times, int ms) {
  for (int i = 0; i < times; i++) {
    digitalWrite(BUZZER, HIGH); digitalWrite(LED, LOW);  delay(ms);
    digitalWrite(BUZZER, LOW);  digitalWrite(LED, HIGH); delay(ms);
  }
  digitalWrite(LED, isOpen ? LOW : HIGH);
}

// Gerakkan servo lalu lepas PWM setelah MOVE_MS supaya tidak terus menahan
// beban (mencegah arus stall melebihi kemampuan charger 2A).
// Pakai LEDC langsung: ESP32Servo selalu mengirim pulsa 90 derajat saat attach,
// membuat servo menyentak ke tengah sebelum ke sudut tujuan.
void moveServo(int angle) {
  uint32_t us = map(-angle, -90, 90, SERVO_MIN_US, SERVO_MAX_US);
  ledcWrite(PIN_SERVO, us * ((1 << SERVO_BITS) - 1) / 20000);
  currentAngle = angle;
  detachAt = millis() + MOVE_MS;
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

// Kunci otomatis setelah pintu ditutup, dan pengingat jika pintu lama terbuka
void serviceDoorLogic() {
  unsigned long now = millis();
  if (isOpen && doorOpenedSinceUnlock && doorClosed && now - doorChangedAt >= AUTOLOCK_MS)
    lock("otomatis");

  // Pengingat hanya saat kunci terbuka; jika sudah terkunci tidak ada yang lupa ditutup
  if (isOpen && !doorClosed && now - doorChangedAt >= REMIND_AFTER_MS &&
      (!lastReminder || now - lastReminder >= REMIND_EVERY_MS)) {
    if (!lastReminder) netEvent("reminder", !isOpen, doorClosed);   // laporkan sekali per kejadian
    lastReminder = now;
    beep(3, 60);
    Serial.printf("PERINGATAN: pintu terbuka %lu detik\n", (now - doorChangedAt) / 1000);
  }
}

void serviceServo() {
  if (detachAt && millis() >= detachAt) { ledcWrite(PIN_SERVO, 0); detachAt = 0; }
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
    serviceServo();
    serviceDoor();
    serviceDoorLogic();
    delay(100);
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
  netEvent("boot", true, doorClosed);
}

void loop() {
  serviceServo();
  serviceDoor();
  serviceDoorLogic();
  handleSerial();
  handleCommands();

  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    delay(50);
    return;
  }

  String uid = uidToString(rfid.uid);
  MFRC522::PICC_Type type = rfid.PICC_GetType(rfid.uid.sak);
  const char *name = lookup(uid);

  // Aksi dulu (servo + buzzer), baru log ke Serial
  bool rejectedLock = false;
  if (uid == OWNER_UID) {
    if (!isOpen) {
      unlock(name);
    } else if (!doorClosed) {
      rejectedLock = true;
      rejectLock(name);
    } else {
      lock(name);
    }
  } else if (name) {
    beep(1, 700);               // ditolak: biiip panjang
    netEvent("denied_card", !isOpen, doorClosed, name);
  } else {
    beep(3, 100);               // asing: bip-bip-bip cepat
    netEvent("unknown_card", !isOpen, doorClosed, "", uid.c_str());
  }

  Serial.println("--------------------------------");
  Serial.printf("UID  : %s\n", uid.c_str());
  Serial.print("Tipe : ");
  Serial.println(rfid.PICC_GetTypeName(type));
  if (rejectedLock)
    Serial.printf("DIKENAL: %s -> DITOLAK, pintu masih terbuka\n", name);
  else if (uid == OWNER_UID)
    Serial.printf("DIKENAL: %s -> %s\n", name, isOpen ? "TERBUKA" : "TERKUNCI");
  else if (name)
    Serial.printf("DIKENAL: %s -> tidak punya akses servo\n", name);
  else
    Serial.println("TIDAK DIKENAL");

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  waitCardRemoved();
}
