// PCDash K-Touch app - shared declarations.
#pragma once
#include <Arduino.h>
#include <lvgl.h>

#define APP_VERSION "PCDash-ktouch 1.6-watchdog"
#define SCREEN_W 800
#define SCREEN_H 480
#define SETUP_AP_NAME "PCDash-KTouch-Setup"
#define MDNS_NAME "pcdash-ktouch"

// hw.cpp
void hw_init();
void hw_lvgl_init();
void hw_backlight(uint8_t percent);
void hw_message(const char *title, const char *body);
uint32_t hw_last_touch_ms();

// wifi_setup.cpp
void wifi_connect_or_portal();   // blocks until joined; shows instructions on screen
void wifi_forget_and_restart();

// net.cpp
void net_start();
// Latest JSON pushed by the PC (copied under a lock). Returns 0 if none yet.
uint32_t net_state_copy(String &out, uint32_t *age_ms);
void net_queue_action(const char *fmt, ...);
#define TILE_W 246
#define TILE_H 160
#define IMG_BYTES (TILE_W * TILE_H * 2)
#define FOLDER_W 384          // the Games / Apps folder pictures (ids _fgames, _fapps)
#define FOLDER_H 340
#define FOLDER_BYTES (FOLDER_W * FOLDER_H * 2)
const uint8_t *net_image(const char *id);   // RGB565 tile picture, or nullptr
uint32_t net_img_version();

// ui.cpp
void ui_build();
void ui_tick();
