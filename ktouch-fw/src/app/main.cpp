// PCDash K-Touch app entry point.
#include "app.h"
#include <WiFi.h>

void setup() {
    Serial.begin(115200);
    delay(100);
    Serial.println("\n=== " APP_VERSION " ===");
    hw_init();
    wifi_connect_or_portal();          // blocks until on WiFi (first run: setup hotspot)
    WiFi.setSleep(false);              // modem power-save drops the link on some routers
    net_start();
    hw_lvgl_init();
    ui_build();
    Serial.printf("ready at %s\n", WiFi.localIP().toString().c_str());
}

// WiFi watchdog (2026-11-15): the K-Touch fell off WiFi at 04:15 and never came
// back, though the screen stayed on. Auto-reconnect alone is not enough on the
// ESP32 (it can stay "connected" with a dead link, or stop retrying).
//  - WiFi down: restart WiFi every 30 s. Never reboot for this: a reboot without
//    WiFi would end up in the setup hotspot and wait there.
//  - WiFi up but nothing from the PC for 3 min: restart WiFi; after 8 min, reboot.
static void net_watchdog() {
    static uint32_t last_check = 0, down_since = 0, last_kick = 0;
    uint32_t now = millis();
    if (now - last_check < 1000) return;
    last_check = now;
    uint32_t age;
    String dummy;
    net_state_copy(dummy, &age);
    if (age == UINT32_MAX) age = now;               // nothing yet since boot
    if (WiFi.status() != WL_CONNECTED) {
        if (!down_since) down_since = now;
        if (now - down_since > 30000 && now - last_kick > 30000) {
            Serial.println("watchdog: WiFi down, restarting WiFi");
            WiFi.disconnect(false);
            WiFi.begin();
            last_kick = now;
        }
        return;
    }
    down_since = 0;
    if (age > 8UL * 60 * 1000) {
        Serial.println("watchdog: PC silent 8 min on a live WiFi link, rebooting");
        delay(200);
        ESP.restart();
    }
    if (age > 3UL * 60 * 1000 && now - last_kick > 3UL * 60 * 1000) {
        Serial.println("watchdog: PC silent 3 min, restarting WiFi");
        WiFi.disconnect(false);
        WiFi.begin();
        last_kick = now;
    }
}

void loop() {
    lv_timer_handler();
    ui_tick();
    net_watchdog();
    delay(5);
}
