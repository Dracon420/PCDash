// WiFi join / first-time setup, kept in its own file: WiFiManager pulls in the
// synchronous WebServer, whose HTTP method names clash with ESPAsyncWebServer's.
//
// First boot (or after "Forget WiFi"): the K-Touch opens an open hotspot named
// SETUP_AP_NAME. Join it from a phone; a page opens to pick the home WiFi and
// type its password. The password is only ever typed by the person, on that page.
#include "app.h"
#include <WiFi.h>
#include <WiFiManager.h>

void wifi_connect_or_portal() {
    WiFi.mode(WIFI_STA);
    WiFi.setHostname(MDNS_NAME);

    WiFiManager wm;
    wm.setConfigPortalTimeout(0);          // wait as long as it takes
    wm.setConnectTimeout(20);
    wm.setTitle("PCDash K-Touch");
    wm.setAPCallback([](WiFiManager *) {
        hw_message("WiFi setup",
                   "1. On your phone, join the WiFi:\n"
                   "      " SETUP_AP_NAME "\n"
                   "2. A setup page opens (or go to 192.168.4.1)\n"
                   "3. Pick your home WiFi - the same one\n"
                   "   as the gaming PC - and enter its password.");
    });

    // After WiFi is set up through the hotspot, restart once: otherwise the
    // portal's web server keeps port 80 and the app's server never answers
    // (seen 2026-11-11 - fine after a reboot, unreachable until then).
    static bool configured_via_portal = false;
    wm.setSaveConfigCallback([]() { configured_via_portal = true; });

    hw_message("Connecting", "Joining WiFi...");
    if (!wm.autoConnect(SETUP_AP_NAME)) {
        hw_message("WiFi failed", "Restarting...");
        delay(3000);
        ESP.restart();
    }
    if (configured_via_portal) {
        hw_message("WiFi saved", "Restarting to start the app...");
        delay(1500);
        ESP.restart();
    }
    WiFi.setAutoReconnect(true);
    WiFi.persistent(true);
    char msg[96];
    snprintf(msg, sizeof(msg), "Connected: %s\nIP %s", WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
    hw_message("WiFi OK", msg);
    delay(800);
}

void wifi_forget_and_restart() {
    WiFiManager wm;
    wm.resetSettings();
    delay(300);
    ESP.restart();
}
