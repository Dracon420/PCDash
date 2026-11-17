// K-Touch hardware test: colour bars + touch dots.
// Proves the RGB panel pins/timings and the GT911 touch wiring before the real app.
// Expected: 8 vertical colour bars, white text, and a dot wherever you touch.
#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <Wire.h>

// ---- pins (fmauNeko/pandatouch-bsp, doc/HARDWARE.md) ----
#define LCD_PCLK 5
#define LCD_DE 38
#define LCD_RST 46
#define LCD_BL 21
#define TP_SDA 2
#define TP_SCL 1
#define TP_INT 40
#define TP_RST 41

// 16-bit RGB565: R3..R7, G2..G7, B3..B7
Arduino_ESP32RGBPanel *bus = new Arduino_ESP32RGBPanel(
    LCD_DE, GFX_NOT_DEFINED /* vsync */, GFX_NOT_DEFINED /* hsync */, LCD_PCLK,
    6, 7, 8, 9, 10,              // R3..R7
    11, 12, 13, 14, 15, 16,      // G2..G7
    17, 18, 48, 47, 39,          // B3..B7
    0, 8, 4, 8,                  // hsync: polarity, front porch, pulse width, back porch
    0, 16, 4, 16,                // vsync: polarity, front porch, pulse width, back porch
    1,                           // pclk active on falling edge
    23000000);                   // 23 MHz pixel clock
Arduino_RGB_Display *gfx = new Arduino_RGB_Display(800, 480, bus, 0, true);

// ---- minimal GT911 ----
static uint8_t tp_addr = 0x5D;

static bool tp_write(uint16_t reg, uint8_t val) {
    Wire.beginTransmission(tp_addr);
    Wire.write(reg >> 8); Wire.write(reg & 0xFF); Wire.write(val);
    return Wire.endTransmission() == 0;
}
static bool tp_read(uint16_t reg, uint8_t *buf, uint8_t n) {
    Wire.beginTransmission(tp_addr);
    Wire.write(reg >> 8); Wire.write(reg & 0xFF);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom((int)tp_addr, (int)n) != n) return false;
    for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
    return true;
}
static void tp_reset() {
    // INT low during reset release selects address 0x5D.
    pinMode(TP_INT, OUTPUT); pinMode(TP_RST, OUTPUT);
    digitalWrite(TP_INT, LOW); digitalWrite(TP_RST, LOW); delay(10);
    digitalWrite(TP_RST, HIGH); delay(10);
    pinMode(TP_INT, INPUT); delay(60);
}

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.println("\n=== PCDash K-Touch hwtest ===");
    Serial.printf("PSRAM %u bytes, flash %u bytes\n", ESP.getPsramSize(), ESP.getFlashChipSize());

    pinMode(LCD_RST, OUTPUT);
    digitalWrite(LCD_RST, LOW); delay(10); digitalWrite(LCD_RST, HIGH); delay(50);

    if (!gfx->begin()) Serial.println("gfx->begin() FAILED");
    pinMode(LCD_BL, OUTPUT);
    digitalWrite(LCD_BL, HIGH);

    const uint16_t bars[] = {RGB565_WHITE, RGB565_YELLOW, RGB565_CYAN, RGB565_GREEN,
                             RGB565_MAGENTA, RGB565_RED, RGB565_BLUE, RGB565_BLACK};
    for (int i = 0; i < 8; i++) gfx->fillRect(i * 100, 0, 100, 330, bars[i]);
    for (int x = 0; x < 800; x++) {           // grey gradient: checks every bit line
        uint8_t v = x * 255 / 799;
        gfx->drawFastVLine(x, 330, 50, gfx->color565(v, v, v));
    }
    gfx->fillRect(0, 380, 800, 100, RGB565_BLACK);
    gfx->setTextColor(RGB565_WHITE);
    gfx->setTextSize(3);
    gfx->setCursor(20, 400);
    gfx->print("PCDash K-Touch test - touch me");
    gfx->flush();

    Wire.begin(TP_SDA, TP_SCL, 400000);
    tp_reset();
    Serial.print("I2C devices:");
    for (uint8_t a = 1; a < 127; a++) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0) Serial.printf(" 0x%02X", a);
    }
    Serial.println();
    uint8_t id[4] = {0};
    Wire.beginTransmission(0x5D);
    if (Wire.endTransmission() != 0) tp_addr = 0x14;
    if (tp_read(0x8140, id, 4)) Serial.printf("touch id: %c%c%c%c at 0x%02X\n", id[0], id[1], id[2], id[3], tp_addr);
    else Serial.println("touch: no reply");
}

void loop() {
    uint8_t st;
    if (tp_read(0x814E, &st, 1) && (st & 0x80)) {
        uint8_t n = st & 0x0F;
        for (uint8_t i = 0; i < n && i < 5; i++) {
            uint8_t p[8];
            if (!tp_read(0x8150 + i * 8, p, 8)) break;
            uint16_t x = p[0] | (p[1] << 8), y = p[2] | (p[3] << 8);
            Serial.printf("touch %u: %u,%u\n", i, x, y);
            gfx->fillCircle(x, y, 8, i ? RGB565_ORANGE : RGB565_RED);
        }
        tp_write(0x814E, 0);
        gfx->flush();
    }
    delay(15);
}
