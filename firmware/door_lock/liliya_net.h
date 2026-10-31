// Koneksi kunci pintu ke server Liliya/Budi (STB).
// Jaringan berjalan di task FreeRTOS sendiri supaya kartu, sensor pintu, dan
// kunci otomatis di loop() tetap jalan walaupun WiFi/server mati.
#pragma once
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "secrets.h"   // WIFI_SSID, WIFI_PASS, LILIYA_URL, DEVICE_TOKEN (tidak ikut dibagikan)

struct NetEvent {
  char type[16];
  char by[24];
  char uid[24];
  char cmdId[16];
  bool locked;
  bool doorClosed;
};

struct NetCmd {
  char cmd[8];
  char id[16];
  char by[24];
};

static QueueHandle_t evQueue, cmdQueue;

// Antrekan kejadian untuk dikirim; jika antrean penuh kejadian dibuang
// daripada menahan loop()
void netEvent(const char *type, bool locked, bool doorClosed,
              const char *by = "", const char *uid = "", const char *cmdId = "") {
  NetEvent e = {};
  strlcpy(e.type, type, sizeof e.type);
  strlcpy(e.by, by, sizeof e.by);
  strlcpy(e.uid, uid, sizeof e.uid);
  strlcpy(e.cmdId, cmdId, sizeof e.cmdId);
  e.locked = locked;
  e.doorClosed = doorClosed;
  xQueueSend(evQueue, &e, 0);
}

bool netNextCmd(NetCmd &c) {
  return xQueueReceive(cmdQueue, &c, 0) == pdTRUE;
}

static void eventTask(void *) {
  NetEvent e;
  for (;;) {
    if (xQueuePeek(evQueue, &e, portMAX_DELAY) != pdTRUE) continue;
    if (WiFi.status() != WL_CONNECTED) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }

    JsonDocument doc;
    doc["type"] = e.type;
    doc["locked"] = e.locked;
    doc["door_closed"] = e.doorClosed;
    if (*e.by) doc["by"] = e.by;
    if (*e.uid) doc["uid"] = e.uid;
    if (*e.cmdId) doc["cmd_id"] = e.cmdId;
    String body;
    serializeJson(doc, body);

    HTTPClient http;
    http.begin(String(LILIYA_URL) + "/api/door-device/event");
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.setTimeout(5000);
    int code = http.POST(body);
    http.end();

    // 4xx tidak akan berhasil walau diulang; selain itu coba lagi nanti
    if (code >= 200 && code < 500) xQueueReceive(evQueue, &e, 0);
    else vTaskDelay(pdMS_TO_TICKS(2000));
  }
}

// Long-poll perintah dari aplikasi/suara; sekaligus menjadi tanda online
static void cmdTask(void *) {
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) { vTaskDelay(pdMS_TO_TICKS(1000)); continue; }
    HTTPClient http;
    http.begin(String(LILIYA_URL) + "/api/door-device/command?wait=20");
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.setTimeout(25000);
    int code = http.GET();
    if (code == 200) {
      JsonDocument doc;
      if (!deserializeJson(doc, http.getString()) && doc["cmd"].is<const char *>()) {
        NetCmd c = {};
        strlcpy(c.cmd, doc["cmd"], sizeof c.cmd);
        strlcpy(c.id, doc["id"] | "", sizeof c.id);
        strlcpy(c.by, doc["by"] | "Aplikasi", sizeof c.by);
        xQueueSend(cmdQueue, &c, 0);
      }
    }
    http.end();
    if (code != 200) vTaskDelay(pdMS_TO_TICKS(3000));
  }
}

void netBegin() {
  evQueue = xQueueCreate(16, sizeof(NetEvent));
  cmdQueue = xQueueCreate(4, sizeof(NetCmd));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  xTaskCreate(eventTask, "liliya_ev", 6144, nullptr, 1, nullptr);
  xTaskCreate(cmdTask, "liliya_cmd", 6144, nullptr, 1, nullptr);
}
