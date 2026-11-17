// K-Touch hardware: 800x480 RGB panel (Arduino_GFX), GT911 touch, LVGL glue.
// Pins and timings proven by hwtest.cpp (2026-11-11); see platformio.ini header.
#include "app.h"
#include <Arduino_GFX_Library.h>
#include <Wire.h>

#define LCD_PCLK 5
#define LCD_DE 38
#define LCD_RST 46
#define LCD_BL 21
#define TP_SDA 2
#define TP_SCL 1
#define TP_INT 40
#define TP_RST 41

static Arduino_ESP32RGBPanel *bus = new Arduino_ESP32RGBPanel(
    LCD_DE, GFX_NOT_DEFINED, GFX_NOT_DEFINED, LCD_PCLK,
    6, 7, 8, 9, 10,              // R3..R7
    11, 12, 13, 14, 15, 16,      // G2..G7
    17, 18, 48, 47, 39,          // B3..B7
    0, 8, 4, 8, 0, 16, 4, 16, 1,
    // 16 MHz, not the 23 MHz the BSP uses: with WiFi running, PSRAM bandwidth is
    // shared and 23 MHz made the panel flicker (2026-11-11). 14 MHz ~ 33 Hz refresh.
    14000000);
Arduino_RGB_Display *gfx = new Arduino_RGB_Display(SCREEN_W, SCREEN_H, bus, 0, true);

// ---------------------------------------------------------------- backlight --
void hw_backlight(uint8_t percent) {
    static bool setup_done = false;
    // 25 kHz / 8-bit. 30 kHz / 11-bit (the BSP's) cannot be set up by the Arduino
    // LEDC wrapper (screen stayed dark), and 5 kHz made the backlight driver
    // whine audibly - both seen 2026-11-11. 25 kHz is above hearing.
    if (!setup_done) { ledcSetup(1, 25000, 8); ledcAttachPin(LCD_BL, 1); setup_done = true; }
    if (percent > 100) percent = 100;
    ledcWrite(1, (uint32_t)percent * 255 / 100);
}

// -------------------------------------------------------------------- touch --
static const uint8_t TP_ADDR = 0x5D;

static bool tp_read(uint16_t reg, uint8_t *buf, uint8_t n) {
    Wire.beginTransmission(TP_ADDR);
    Wire.write(reg >> 8); Wire.write(reg & 0xFF);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom((int)TP_ADDR, (int)n) != n) return false;
    for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
    return true;
}
static void tp_clear() {
    Wire.beginTransmission(TP_ADDR);
    Wire.write(0x81); Wire.write(0x4E); Wire.write(0);
    Wire.endTransmission();
}

static volatile uint32_t last_touch_ms = 0;
uint32_t hw_last_touch_ms() { return last_touch_ms; }

static void lv_touch_read(lv_indev_drv_t *, lv_indev_data_t *data) {
    static int16_t lx = 0, ly = 0;
    static bool down = false;
    uint8_t st;
    if (tp_read(0x814E, &st, 1) && (st & 0x80)) {
        uint8_t n = st & 0x0F;
        if (n > 0) {
            uint8_t p[4];
            if (tp_read(0x8150, p, 4)) {
                lx = p[0] | (p[1] << 8);
                ly = p[2] | (p[3] << 8);
            }
            down = true;
            last_touch_ms = millis();
        } else {
            down = false;
        }
        tp_clear();
    }
    data->point.x = lx;
    data->point.y = ly;
    data->state = down ? LV_INDEV_STATE_PR : LV_INDEV_STATE_REL;
}

// ------------------------------------------------------------------ display --
static void lv_flush(lv_disp_drv_t *drv, const lv_area_t *a, lv_color_t *px) {
    uint32_t w = a->x2 - a->x1 + 1, h = a->y2 - a->y1 + 1;
    gfx->draw16bitRGBBitmap(a->x1, a->y1, (uint16_t *)&px->full, w, h);
    lv_disp_flush_ready(drv);
}

void hw_init() {
    pinMode(LCD_RST, OUTPUT);
    digitalWrite(LCD_RST, LOW); delay(10); digitalWrite(LCD_RST, HIGH); delay(50);
    gfx->begin();
    gfx->fillScreen(0x0000);
    hw_backlight(80);

    Wire.begin(TP_SDA, TP_SCL, 400000);
    pinMode(TP_INT, OUTPUT); pinMode(TP_RST, OUTPUT);       // INT low at reset => 0x5D
    digitalWrite(TP_INT, LOW); digitalWrite(TP_RST, LOW); delay(10);
    digitalWrite(TP_RST, HIGH); delay(10);
    pinMode(TP_INT, INPUT); delay(60);
}

void hw_lvgl_init() {
    lv_init();
    static lv_disp_draw_buf_t buf;
    // Draw buffers in INTERNAL RAM, not PSRAM: the panel streams its framebuffer
    // from PSRAM, and rendering there as well starved it (twitching, 2026-11-11).
    const uint32_t lines = 40;   // 2 x 64 KB
    lv_color_t *b1 = (lv_color_t *)heap_caps_malloc(SCREEN_W * lines * sizeof(lv_color_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    lv_color_t *b2 = (lv_color_t *)heap_caps_malloc(SCREEN_W * lines * sizeof(lv_color_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    lv_disp_draw_buf_init(&buf, b1, b2, SCREEN_W * lines);

    static lv_disp_drv_t disp;
    lv_disp_drv_init(&disp);
    disp.hor_res = SCREEN_W;
    disp.ver_res = SCREEN_H;
    disp.flush_cb = lv_flush;
    disp.draw_buf = &buf;
    lv_disp_drv_register(&disp);

    static lv_indev_drv_t indev;
    lv_indev_drv_init(&indev);
    indev.type = LV_INDEV_TYPE_POINTER;
    indev.read_cb = lv_touch_read;
    lv_indev_drv_register(&indev);
}

// Plain text screen drawn straight to the panel, for before LVGL runs (WiFi setup).
void hw_message(const char *title, const char *body) {
    gfx->fillScreen(0x0000);
    gfx->setTextColor(0x4E1F);
    gfx->setTextSize(4);
    gfx->setCursor(40, 60);
    gfx->print(title);
    gfx->setTextColor(0xFFFF);
    gfx->setTextSize(3);
    int y = 140;
    const char *p = body;
    while (*p) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        char line[64];
        len = len < sizeof(line) - 1 ? len : sizeof(line) - 1;
        memcpy(line, p, len); line[len] = 0;
        gfx->setCursor(40, y);
        gfx->print(line);
        y += 40;
        if (!nl) break;
        p = nl + 1;
    }
    gfx->flush();
}
