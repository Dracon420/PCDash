// K-Touch UI: header strip + 5 tabs (Apps, Servers, Stream, PC + volume mixer, Screen).
// Everything shown comes from the JSON the PC pushes to /state; every button
// queues an action string that goes back in the reply to the next push.
#include "app.h"
#include <ArduinoJson.h>
#include <WiFi.h>
#include <stdarg.h>

// ------------------------------------------------------------------ colours --
// Cyberpunk HUD palette - same as the Onn screen's theme.css (2026-11-11).
#define C_BG     lv_color_hex(0x040509)
#define C_CARD   lv_color_hex(0x0B0E1C)
#define C_CARD2  lv_color_hex(0x121730)
#define C_LINE   lv_color_hex(0x16304A)
#define C_TEXT   lv_color_hex(0xE8F6FF)
#define C_DIM    lv_color_hex(0x7D8BB8)
#define C_FAINT  lv_color_hex(0x3F4870)
#define C_ACCENT lv_color_hex(0x00F0FF)
#define C_VIOLET lv_color_hex(0xB14CFF)
#define C_YELLOW lv_color_hex(0xFCEE0A)
#define C_OK     lv_color_hex(0x00FF9C)
#define C_WARN   lv_color_hex(0xFCEE0A)
#define C_BAD    lv_color_hex(0xFF2A6D)
#define C_LIVE   lv_color_hex(0xFF2A6D)


// ------------------------------------------------- change-only setters --
// The RGB panel scans its framebuffer out of PSRAM, and every LVGL redraw also
// hits PSRAM; too many redraws and the panel underruns and twitches (seen
// 2026-11-11). LVGL invalidates on every set, even to the same value, so all
// updates go through these, which only touch the object when something changed.
// set_fmt also uses newlib's vsnprintf: LVGL's own formatter prints "%.1f" as "f".
static void set_txt(lv_obj_t *l, const char *t) {
    const char *cur = lv_label_get_text(l);
    if (!cur || strcmp(cur, t) != 0) lv_label_set_text(l, t);
}
static void set_fmt(lv_obj_t *l, const char *fmt, ...) {
    char buf[128];
    va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    set_txt(l, buf);
}
static void set_bg(lv_obj_t *o, lv_color_t c, lv_style_selector_t sel) {
    if (lv_obj_get_style_bg_color(o, sel).full != c.full) lv_obj_set_style_bg_color(o, c, sel);
}
static void set_tc(lv_obj_t *o, lv_color_t c, lv_style_selector_t sel) {
    if (lv_obj_get_style_text_color(o, sel).full != c.full) lv_obj_set_style_text_color(o, c, sel);
}
static void set_arc(lv_obj_t *o, lv_color_t c, lv_style_selector_t sel) {
    if (lv_obj_get_style_arc_color(o, sel).full != c.full) lv_obj_set_style_arc_color(o, c, sel);
}
static void show(lv_obj_t *o, bool on) {
    bool hidden = lv_obj_has_flag(o, LV_OBJ_FLAG_HIDDEN);
    if (on && hidden) lv_obj_clear_flag(o, LV_OBJ_FLAG_HIDDEN);
    if (!on && !hidden) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
}

static DynamicJsonDocument doc(20 * 1024);
static uint32_t last_seq = 0;
static bool fresh = false;

// ----------------------------------------------------------------- helpers --
static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, lv_color_t color, const char *txt = "") {
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    set_tc(l, color, 0);
    set_txt(l, txt);
    return l;
}

static lv_obj_t *card(lv_obj_t *parent) {
    lv_obj_t *c = lv_obj_create(parent);
    set_bg(c, C_CARD, 0);
    lv_obj_set_style_bg_grad_color(c, lv_color_hex(0x05060C), 0);
    lv_obj_set_style_bg_grad_dir(c, LV_GRAD_DIR_VER, 0);
    lv_obj_set_style_border_color(c, C_ACCENT, 0);
    lv_obj_set_style_border_opa(c, LV_OPA_50, 0);
    lv_obj_set_style_border_width(c, 1, 0);
    lv_obj_set_style_radius(c, 3, 0);
    lv_obj_set_style_pad_all(c, 12, 0);
    lv_obj_clear_flag(c, LV_OBJ_FLAG_SCROLLABLE);
    return c;
}

static lv_obj_t *button(lv_obj_t *parent, const char *txt, lv_color_t bg, const lv_font_t *font = &lv_font_montserrat_20) {
    lv_obj_t *b = lv_btn_create(parent);
    set_bg(b, bg, 0);
    lv_obj_set_style_radius(b, 3, 0);
    lv_obj_set_style_border_width(b, 1, 0);
    lv_obj_set_style_border_color(b, C_ACCENT, 0);
    lv_obj_set_style_border_opa(b, LV_OPA_40, 0);
    lv_obj_set_style_bg_color(b, C_ACCENT, LV_STATE_PRESSED);   // flash cyan when pressed
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_t *l = label(b, font, C_TEXT, txt);
    lv_obj_center(l);
    return b;
}

static void btn_text(lv_obj_t *b, const char *txt) {
    set_txt(lv_obj_get_child(b, 0), txt);
}

static void fmt_hms(char *buf, size_t n, uint32_t s) {
    snprintf(buf, n, "%u:%02u:%02u", (unsigned)(s / 3600), (unsigned)(s / 60 % 60), (unsigned)(s % 60));
}

// --------------------------------------------------------------- toast/confirm --
static lv_obj_t *toast_lbl;
static uint32_t toast_until = 0;

static void toast(const char *msg) {
    set_txt(toast_lbl, msg);
    lv_obj_clear_flag(toast_lbl, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(toast_lbl);
    toast_until = millis() + 2200;
}

// A confirm dialog that, on "Yes", queues `action` and shows `done_msg`.
static char pending_action[96], pending_msg[96];

static void confirm_cb(lv_event_t *e) {
    lv_obj_t *mbox = lv_event_get_current_target(e);
    const char *txt = lv_msgbox_get_active_btn_text(mbox);
    if (txt && strcmp(txt, "Yes") == 0) {
        net_queue_action("%s", pending_action);
        toast(pending_msg);
    }
    lv_msgbox_close(mbox);
}

static void confirm(const char *question, const char *action, const char *done_msg) {
    static const char *btns[] = {"Yes", "No", ""};
    strlcpy(pending_action, action, sizeof(pending_action));
    strlcpy(pending_msg, done_msg, sizeof(pending_msg));
    lv_obj_t *m = lv_msgbox_create(NULL, "Are you sure?", question, btns, false);
    lv_obj_set_width(m, 460);
    lv_obj_set_style_text_font(m, &lv_font_montserrat_24, 0);
    lv_obj_t *bm = lv_msgbox_get_btns(m);
    lv_obj_set_height(bm, 70);
    lv_obj_add_event_cb(m, confirm_cb, LV_EVENT_VALUE_CHANGED, NULL);
    lv_obj_center(m);
}

// ---------------------------------------------------------------- header --
static lv_obj_t *hdr_live, *hdr_time, *hdr_net;

static void build_header(lv_obj_t *scr) {
    lv_obj_t *h = lv_obj_create(scr);
    lv_obj_set_size(h, SCREEN_W, 44);
    lv_obj_align(h, LV_ALIGN_TOP_MID, 0, 0);
    set_bg(h, C_BG, 0);
    lv_obj_set_style_border_side(h, LV_BORDER_SIDE_BOTTOM, 0);
    lv_obj_set_style_border_width(h, 2, 0);
    lv_obj_set_style_border_color(h, C_ACCENT, 0);
    lv_obj_set_style_border_width(h, 0, 0);
    lv_obj_set_style_radius(h, 0, 0);
    lv_obj_set_style_pad_hor(h, 14, 0);
    lv_obj_clear_flag(h, LV_OBJ_FLAG_SCROLLABLE);
    hdr_live = label(h, &lv_font_montserrat_20, C_ACCENT, "OFF AIR");
    lv_obj_align(hdr_live, LV_ALIGN_LEFT_MID, 0, 0);
    hdr_time = label(h, &lv_font_montserrat_24, C_YELLOW, "");
    lv_obj_align(hdr_time, LV_ALIGN_CENTER, 0, 0);
    hdr_net = label(h, &lv_font_montserrat_16, C_DIM, "");
    lv_obj_align(hdr_net, LV_ALIGN_RIGHT_MID, 0, 0);
}

// ------------------------------------------------------------------ Apps --
// First tab (2026-11-12): two folder tiles, Games and Apps. Tapping one shows
// its tiles behind a Back tile. Each tile: its picture (when the PC has sent
// one) with the name bottom-left, or its colour with the name centred.
// Rebuilt only when the lists, the pictures or the open folder change.
static lv_obj_t *apps_grid;
static String apps_sig;
static int8_t open_folder = -1;          // -1 = the two folders, 0 = games, 1 = apps
static bool folder_dirty = false;

struct GameTile { char id[24]; char name[40]; lv_img_dsc_t dsc; };
#define MAX_TILES 20
static GameTile tiles[MAX_TILES];
static lv_img_dsc_t folder_dsc[2];

static void game_cb(lv_event_t *e) {
    GameTile *t = (GameTile *)lv_event_get_user_data(e);
    net_queue_action("launch:%s", t->id);
    char msg[80];
    snprintf(msg, sizeof(msg), LV_SYMBOL_PLAY " Starting %s...", t->name);
    toast(msg);
}

static void folder_cb(lv_event_t *e) {
    open_folder = (int8_t)(intptr_t)lv_event_get_user_data(e);
    folder_dirty = true;                 // rebuilt from ui_tick, not inside this click
}

static void build_apps(lv_obj_t *tab) {
    lv_obj_set_style_pad_all(tab, 10, 0);
    apps_grid = lv_obj_create(tab);
    lv_obj_set_size(apps_grid, lv_pct(100), lv_pct(100));
    lv_obj_set_style_bg_opa(apps_grid, LV_OPA_0, 0);
    lv_obj_set_style_border_width(apps_grid, 0, 0);
    lv_obj_set_style_pad_all(apps_grid, 0, 0);
    lv_obj_set_style_pad_gap(apps_grid, 12, 0);
    lv_obj_set_flex_flow(apps_grid, LV_FLEX_FLOW_ROW_WRAP);
    label(apps_grid, &lv_font_montserrat_20, C_DIM, "Waiting for the PC...");
}

static lv_obj_t *tile_btn(lv_color_t bg, lv_color_t border) {
    lv_obj_t *b = lv_btn_create(apps_grid);
    lv_obj_set_size(b, TILE_W, TILE_H);
    lv_obj_set_style_pad_all(b, 0, 0);
    lv_obj_set_style_radius(b, 3, 0);
    lv_obj_set_style_clip_corner(b, true, 0);
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_style_border_color(b, border, 0);
    lv_obj_set_style_border_opa(b, LV_OPA_70, 0);
    lv_obj_set_style_border_color(b, C_YELLOW, LV_STATE_PRESSED);
    lv_obj_set_style_bg_color(b, bg, 0);
    return b;
}

static void tile_picture(lv_obj_t *b, lv_img_dsc_t *dsc, const uint8_t *px) {
    memset(dsc, 0, sizeof(*dsc));
    dsc->header.cf = LV_IMG_CF_TRUE_COLOR;
    dsc->header.w = TILE_W;
    dsc->header.h = TILE_H;
    dsc->data_size = IMG_BYTES;
    dsc->data = px;
    lv_obj_t *img = lv_img_create(b);
    lv_img_set_src(img, dsc);
    lv_obj_center(img);
    lv_obj_clear_flag(img, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(img, LV_OBJ_FLAG_EVENT_BUBBLE);
}

// A folder: its own art from the PC (make_tile_art.py draws the title into it),
// or until that arrives, the first item picture with the name on top.
static void folder_tile(intptr_t idx, const char *name, JsonArray items) {
    lv_obj_t *b = tile_btn(idx ? lv_color_hex(0x0E3A4A) : lv_color_hex(0x2A1450), idx ? C_ACCENT : C_VIOLET);
    lv_obj_set_size(b, FOLDER_W, FOLDER_H);
    lv_obj_add_event_cb(b, folder_cb, LV_EVENT_CLICKED, (void *)idx);
    const uint8_t *art = net_image(idx ? "_fapps" : "_fgames");
    if (art) {
        memset(&folder_dsc[idx], 0, sizeof(folder_dsc[idx]));
        folder_dsc[idx].header.cf = LV_IMG_CF_TRUE_COLOR;
        folder_dsc[idx].header.w = FOLDER_W;
        folder_dsc[idx].header.h = FOLDER_H;
        folder_dsc[idx].data_size = FOLDER_BYTES;
        folder_dsc[idx].data = art;
        lv_obj_t *img = lv_img_create(b);
        lv_img_set_src(img, &folder_dsc[idx]);
        lv_obj_center(img);
        lv_obj_clear_flag(img, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_flag(img, LV_OBJ_FLAG_EVENT_BUBBLE);
    } else {
        const uint8_t *px = nullptr;
        for (JsonObject g : items) if ((px = net_image(g["id"] | ""))) break;
        if (px) tile_picture(b, &folder_dsc[idx], px);
        lv_obj_t *l = label(b, &lv_font_montserrat_28, lv_color_white(), "");
        set_fmt(l, "%s  %s", idx ? LV_SYMBOL_DIRECTORY : LV_SYMBOL_PLAY, name);
        lv_obj_align(l, LV_ALIGN_BOTTOM_LEFT, 14, -34);
    }
    lv_obj_t *c = label(b, &lv_font_montserrat_16, C_TEXT, "");
    set_fmt(c, "%u %s", (unsigned)items.size(), idx ? "apps" : "games");
    lv_obj_align(c, art ? LV_ALIGN_BOTTOM_RIGHT : LV_ALIGN_BOTTOM_LEFT, art ? -18 : 16, -16);
}

static void item_tile(GameTile &t, JsonObject g) {
    strlcpy(t.id, g["id"] | "", sizeof(t.id));
    strlcpy(t.name, g["short"] | (g["name"] | "?"), sizeof(t.name));
    const char *col = g["color"] | "#4cc2ff";
    lv_obj_t *b = tile_btn(lv_color_hex(strtoul(col + 1, NULL, 16)), C_ACCENT);
    lv_obj_add_event_cb(b, game_cb, LV_EVENT_CLICKED, &t);
    const uint8_t *px = net_image(t.id);
    if (px) tile_picture(b, &t.dsc, px);
    lv_obj_t *l = label(b, &lv_font_montserrat_24, lv_color_white(), t.name);
    if (px) {   // the PC darkens the bottom of each picture so this stays readable
        lv_obj_align(l, LV_ALIGN_BOTTOM_LEFT, 12, -8);
    } else {
        lv_obj_center(l);
    }
}

static void update_apps(JsonArray games, JsonArray apps) {
    String sig = String(net_img_version()) + ":" + String(open_folder) + ":";
    for (JsonObject g : games) { sig += (const char *)(g["id"] | ""); sig += '|'; }
    sig += '#';
    for (JsonObject g : apps) { sig += (const char *)(g["id"] | ""); sig += '|'; }
    folder_dirty = false;
    if (sig == apps_sig) return;
    apps_sig = sig;
    lv_obj_clean(apps_grid);

    if (open_folder < 0) {
        folder_tile(0, "Games", games);
        folder_tile(1, "Apps", apps);
        return;
    }
    // Back tile first, in the folder's own colour, so it is always top-left.
    lv_obj_t *b = tile_btn(C_CARD2, open_folder ? C_ACCENT : C_VIOLET);
    lv_obj_add_event_cb(b, folder_cb, LV_EVENT_CLICKED, (void *)(intptr_t)-1);
    lv_obj_t *l = label(b, &lv_font_montserrat_24, C_TEXT, "");
    set_fmt(l, LV_SYMBOL_LEFT "  %s", open_folder ? "Apps" : "Games");
    lv_obj_center(l);

    JsonArray list = open_folder ? apps : games;
    uint8_t i = 0;
    for (JsonObject g : list) {
        if (i >= MAX_TILES) break;
        item_tile(tiles[i++], g);
    }
}

// ---------------------------------------------------------------- Screen --
// Fifth tab (2026-11-12): which screen each game and app opens on. Every row
// is Samsung / TV / Onn; the top row sets all games at once. A tap is saved on
// the PC and moves the thing there straight away if it is open. Highlights
// always come from the PC's state, so they show what the PC really has.
static lv_obj_t *scr_list;
static String scr_sig;
struct ScrRow { char id[24]; char name[40]; lv_obj_t *b[3]; };
static ScrRow srows[MAX_TILES + 1];
static uint8_t srow_n = 0;
static const char *SCR_KEY[3] = {"samsung", "tv", "onn"};
static const char *SCR_NAME[3] = {"Samsung", "TV", "Onn"};

static void scr_paint(ScrRow &r, const char *cur, JsonObject have) {
    for (int i = 0; i < 3; i++) {
        bool on = cur && strcmp(cur, SCR_KEY[i]) == 0;
        bool there = have.isNull() ? true : (have[SCR_KEY[i]] | true);
        set_bg(r.b[i], on ? C_ACCENT : C_CARD2, 0);
        set_tc(lv_obj_get_child(r.b[i], 0), on ? C_BG : (there ? C_TEXT : C_FAINT), 0);
    }
}

static void scr_cb(lv_event_t *e) {
    ScrRow *r = (ScrRow *)lv_event_get_user_data(e);
    lv_obj_t *b = lv_event_get_target(e);
    int i = (int)(intptr_t)lv_obj_get_user_data(b);
    net_queue_action("screen:%s:%s", r->id, SCR_KEY[i]);
    scr_paint(*r, SCR_KEY[i], JsonObject());          // show it at once; the PC confirms next push
    char msg[80];
    snprintf(msg, sizeof(msg), LV_SYMBOL_IMAGE " %s " LV_SYMBOL_RIGHT " %s", r->name, SCR_NAME[i]);
    toast(msg);
}

static void build_screen(lv_obj_t *tab) {
    lv_obj_set_style_pad_all(tab, 8, 0);
    scr_list = lv_obj_create(tab);
    lv_obj_set_size(scr_list, lv_pct(100), lv_pct(100));
    lv_obj_set_style_bg_opa(scr_list, LV_OPA_0, 0);
    lv_obj_set_style_border_width(scr_list, 0, 0);
    lv_obj_set_style_pad_all(scr_list, 0, 0);
    lv_obj_set_style_pad_row(scr_list, 6, 0);
    lv_obj_set_flex_flow(scr_list, LV_FLEX_FLOW_COLUMN);
    label(scr_list, &lv_font_montserrat_20, C_DIM, "Waiting for the PC...");
}

static void scr_heading(const char *txt) {
    lv_obj_t *h = label(scr_list, &lv_font_montserrat_16, C_YELLOW, txt);
    lv_obj_set_style_pad_top(h, 6, 0);
}

static void scr_row(const char *id, const char *name, bool strong) {
    if (srow_n >= MAX_TILES + 1) return;
    ScrRow &r = srows[srow_n++];
    strlcpy(r.id, id, sizeof(r.id));
    strlcpy(r.name, name, sizeof(r.name));
    lv_obj_t *row = card(scr_list);
    lv_obj_set_size(row, lv_pct(100), 58);
    lv_obj_set_style_pad_all(row, 6, 0);
    if (strong) lv_obj_set_style_border_color(row, C_VIOLET, 0);
    lv_obj_t *l = label(row, strong ? &lv_font_montserrat_24 : &lv_font_montserrat_20, C_TEXT, name);
    lv_obj_align(l, LV_ALIGN_LEFT_MID, 8, 0);
    for (int i = 2; i >= 0; i--) {
        lv_obj_t *b = button(row, SCR_NAME[i], C_CARD2);
        lv_obj_set_size(b, 120, 44);
        lv_obj_align(b, LV_ALIGN_RIGHT_MID, -(2 - i) * 128, 0);
        lv_obj_set_user_data(b, (void *)(intptr_t)i);
        lv_obj_add_event_cb(b, scr_cb, LV_EVENT_CLICKED, &r);
        r.b[i] = b;
    }
}

static void update_screen(JsonObject sc, JsonArray games, JsonArray apps) {
    String sig;
    for (JsonObject g : games) { sig += (const char *)(g["id"] | ""); sig += '|'; }
    sig += '#';
    for (JsonObject g : apps) { sig += (const char *)(g["id"] | ""); sig += '|'; }
    if (sig != scr_sig) {
        scr_sig = sig;
        lv_obj_clean(scr_list);
        srow_n = 0;
        scr_row("games", LV_SYMBOL_PLAY "  All games", true);
        scr_heading("GAMES");
        for (JsonObject g : games) scr_row(g["id"] | "", g["short"] | (g["name"] | "?"), false);
        scr_heading("APPS");
        for (JsonObject g : apps) scr_row(g["id"] | "", g["short"] | (g["name"] | "?"), false);
    }
    if (sc.isNull()) return;
    JsonObject assign = sc["assign"];
    for (uint8_t i = 0; i < srow_n; i++) {
        ScrRow &r = srows[i];
        const char *cur = strcmp(r.id, "games") == 0 ? (sc["game"] | "") : (assign[r.id] | "");
        scr_paint(r, cur, sc);
    }
}

// --------------------------------------------------------------- Servers --
static lv_obj_t *srv_list;
static String srv_sig;
struct SrvRow { lv_obj_t *dot, *name, *meta, *players, *b1, *b2; char id[40]; char nm[40]; };
static SrvRow rows[10];
static uint8_t row_n = 0;

static void srv_cb(lv_event_t *e) {
    SrvRow *r = (SrvRow *)lv_event_get_user_data(e);
    lv_obj_t *b = lv_event_get_target(e);
    const char *act = (const char *)lv_obj_get_user_data(b);
    if (!act || !*act) return;
    char q[120], a[96], m[96];
    snprintf(a, sizeof(a), "server:%s:%s", r->id, act);
    snprintf(m, sizeof(m), "%s sent to %s", act, r->nm);
    if (strcmp(act, "start") == 0) {
        net_queue_action("%s", a);
        toast(m);
        return;
    }
    snprintf(q, sizeof(q), "%s %s?\nAnyone playing will be disconnected.", strcmp(act, "stop") == 0 ? "Stop" : "Restart", r->nm);
    confirm(q, a, m);
}

static void build_servers(lv_obj_t *tab) {
    lv_obj_set_style_pad_all(tab, 10, 0);
    srv_list = lv_obj_create(tab);
    lv_obj_set_size(srv_list, lv_pct(100), lv_pct(100));
    lv_obj_set_style_bg_opa(srv_list, LV_OPA_0, 0);
    lv_obj_set_style_border_width(srv_list, 0, 0);
    lv_obj_set_style_pad_all(srv_list, 0, 0);
    lv_obj_set_style_pad_gap(srv_list, 8, 0);
    lv_obj_set_flex_flow(srv_list, LV_FLEX_FLOW_COLUMN);
    label(srv_list, &lv_font_montserrat_20, C_DIM, "Waiting for the PC...");
}

static void update_servers(JsonArray servers) {
    String sig;
    for (JsonObject s : servers) { sig += (const char *)(s["id"] | ""); sig += '|'; }
    if (sig != srv_sig) {
        srv_sig = sig;
        lv_obj_clean(srv_list);
        row_n = 0;
        for (JsonObject s : servers) {
            if (row_n >= 10) break;
            SrvRow &r = rows[row_n++];
            strlcpy(r.id, s["id"] | "", sizeof(r.id));
            strlcpy(r.nm, s["name"] | "", sizeof(r.nm));
            lv_obj_t *c = card(srv_list);
            lv_obj_set_size(c, lv_pct(100), 64);
            lv_obj_set_style_pad_ver(c, 6, 0);
            r.dot = lv_obj_create(c);
            lv_obj_set_size(r.dot, 16, 16);
            lv_obj_set_style_radius(r.dot, LV_RADIUS_CIRCLE, 0);
            lv_obj_set_style_border_width(r.dot, 0, 0);
            lv_obj_align(r.dot, LV_ALIGN_LEFT_MID, 0, 0);
            r.name = label(c, &lv_font_montserrat_20, C_TEXT, r.nm);
            lv_obj_align(r.name, LV_ALIGN_TOP_LEFT, 28, -2);
            r.meta = label(c, &lv_font_montserrat_14, C_DIM, "");
            lv_obj_align(r.meta, LV_ALIGN_BOTTOM_LEFT, 28, 2);
            r.players = label(c, &lv_font_montserrat_20, C_DIM, "");
            lv_obj_align(r.players, LV_ALIGN_RIGHT_MID, -270, 0);
            r.b1 = button(c, "", C_CARD2, &lv_font_montserrat_20);
            lv_obj_set_size(r.b1, 120, 46);
            lv_obj_align(r.b1, LV_ALIGN_RIGHT_MID, -128, 0);
            r.b2 = button(c, "", C_CARD2, &lv_font_montserrat_20);
            lv_obj_set_size(r.b2, 120, 46);
            lv_obj_align(r.b2, LV_ALIGN_RIGHT_MID, 0, 0);
            lv_obj_add_event_cb(r.b1, srv_cb, LV_EVENT_CLICKED, &r);
            lv_obj_add_event_cb(r.b2, srv_cb, LV_EVENT_CLICKED, &r);
        }
    }
    uint8_t i = 0;
    for (JsonObject s : servers) {
        if (i >= row_n) break;
        SrvRow &r = rows[i++];
        bool on = s["on"] | false;
        const char *note = s["note"] | "";
        set_bg(r.dot, on ? C_OK : (*note ? lv_color_hex(0x6B8BB5) : C_FAINT), 0);
        char meta[96];
        if (on) snprintf(meta, sizeof(meta), "%s - %s - up %s", (const char *)(s["game"] | ""), (const char *)(s["host"] | ""), (const char *)(s["uptime"] | ""));
        else snprintf(meta, sizeof(meta), "%s - %s - %s", (const char *)(s["game"] | ""), (const char *)(s["host"] | ""), *note ? note : "stopped");
        set_txt(r.meta, meta);
        if (on && !s["players"].isNull()) set_fmt(r.players, "%d on", (int)s["players"]);
        else set_txt(r.players, "");
        // Buttons: running -> Restart / Stop, stopped -> Start (right) only.
        if (on) {
            btn_text(r.b1, "Restart"); lv_obj_set_user_data(r.b1, (void *)"restart"); show(r.b1, true);
            btn_text(r.b2, "Stop"); lv_obj_set_user_data(r.b2, (void *)"stop");
            set_bg(r.b2, lv_color_hex(0x3A1018), 0);
        } else {
            show(r.b1, false);
            btn_text(r.b2, "Start"); lv_obj_set_user_data(r.b2, (void *)"start");
            set_bg(r.b2, lv_color_hex(0x12324A), 0);
        }
    }
}

// ---------------------------------------------------------------- Stream --
static lv_obj_t *st_live, *st_rec, *st_health, *b_live, *b_rec, *b_pause, *b_clip, *b_mic, *b_cam, *scene_row;
static String scenes_sig;
static bool s_live = false, s_rec = false, s_clips = false, s_obs = false;

static void stream_cb(lv_event_t *e) {
    const char *what = (const char *)lv_event_get_user_data(e);
    if (!s_obs) { toast("OBS isn't open on the PC"); return; }
    if (strcmp(what, "live") == 0) {
        if (s_live) confirm("End the stream?", "obs:stream_stop", "Ending stream...");
        else confirm("Go LIVE now?", "obs:stream_start", "Going live...");
    } else if (strcmp(what, "rec") == 0) {
        if (s_rec) confirm("Stop recording?", "obs:record_stop", "Recording saved");
        else { net_queue_action("obs:record_start"); toast("Recording..."); }
    } else if (strcmp(what, "pause") == 0) {
        net_queue_action("obs:record_pause");
    } else if (strcmp(what, "clip") == 0) {
        if (!s_clips) { toast("Clips run while live or recording"); return; }
        net_queue_action("obs:replay_save"); toast(LV_SYMBOL_OK " Clip saved");
    } else if (strcmp(what, "mic") == 0) {
        net_queue_action("obs:mic_toggle");
    } else if (strcmp(what, "cam") == 0) {
        net_queue_action("obs:cam_toggle");
    }
}

static void scene_cb(lv_event_t *e) {
    lv_obj_t *b = lv_event_get_target(e);
    net_queue_action("scene:%s", lv_label_get_text(lv_obj_get_child(b, 0)));
}

static lv_obj_t *stream_btn(lv_obj_t *parent, const char *txt, const char *what, lv_color_t bg) {
    lv_obj_t *b = button(parent, txt, bg, &lv_font_montserrat_24);
    lv_obj_add_event_cb(b, stream_cb, LV_EVENT_CLICKED, (void *)what);
    return b;
}

static void build_stream(lv_obj_t *tab) {
    lv_obj_set_style_pad_all(tab, 10, 0);
    lv_obj_t *top = card(tab);
    lv_obj_set_size(top, lv_pct(100), 70);
    lv_obj_align(top, LV_ALIGN_TOP_MID, 0, 0);
    st_live = label(top, &lv_font_montserrat_32, C_DIM, "OFF AIR");
    lv_obj_align(st_live, LV_ALIGN_LEFT_MID, 4, 0);
    st_rec = label(top, &lv_font_montserrat_24, C_LIVE, "");
    lv_obj_align(st_rec, LV_ALIGN_CENTER, 40, 0);
    st_health = label(top, &lv_font_montserrat_16, C_DIM, "");
    lv_obj_align(st_health, LV_ALIGN_RIGHT_MID, -4, 0);
    lv_obj_set_style_text_align(st_health, LV_TEXT_ALIGN_RIGHT, 0);

    lv_obj_t *grid = lv_obj_create(tab);
    lv_obj_set_size(grid, lv_pct(100), 196);
    lv_obj_align(grid, LV_ALIGN_TOP_MID, 0, 80);
    lv_obj_set_style_bg_opa(grid, LV_OPA_0, 0);
    lv_obj_set_style_border_width(grid, 0, 0);
    lv_obj_set_style_pad_all(grid, 0, 0);
    lv_obj_set_style_pad_gap(grid, 10, 0);
    lv_obj_set_flex_flow(grid, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_clear_flag(grid, LV_OBJ_FLAG_SCROLLABLE);
    b_live = stream_btn(grid, LV_SYMBOL_VIDEO " Go Live", "live", lv_color_hex(0x3A1018));
    b_rec = stream_btn(grid, LV_SYMBOL_STOP " Record", "rec", C_CARD2);
    b_pause = stream_btn(grid, LV_SYMBOL_PAUSE " Pause", "pause", C_CARD2);
    b_clip = stream_btn(grid, LV_SYMBOL_SAVE " Save Clip", "clip", C_CARD2);
    b_mic = stream_btn(grid, LV_SYMBOL_AUDIO " Mic", "mic", C_CARD2);
    b_cam = stream_btn(grid, LV_SYMBOL_EYE_OPEN " Webcam", "cam", C_CARD2);
    lv_obj_t *bs[] = {b_live, b_rec, b_pause, b_clip, b_mic, b_cam};
    for (lv_obj_t *b : bs) lv_obj_set_size(b, 248, 92);

    scene_row = lv_obj_create(tab);
    lv_obj_set_size(scene_row, lv_pct(100), 70);
    lv_obj_align(scene_row, LV_ALIGN_TOP_MID, 0, 286);
    set_bg(scene_row, C_CARD, 0);
    lv_obj_set_style_border_color(scene_row, C_ACCENT, 0);
    lv_obj_set_style_border_opa(scene_row, LV_OPA_50, 0);
    lv_obj_set_style_radius(scene_row, 3, 0);
    lv_obj_set_style_pad_all(scene_row, 8, 0);
    lv_obj_set_style_pad_gap(scene_row, 8, 0);
    lv_obj_set_flex_flow(scene_row, LV_FLEX_FLOW_ROW);
}

static void update_stream(JsonObject s) {
    s_obs = s["obs"] | false;
    s_live = s["live"] | false;
    s_rec = s["rec"] | false;
    s_clips = s["clips"] | false;
    bool paused = s["paused"] | false, mic_muted = s["mic_muted"] | false, cam = s["cam"] | false;
    char t[16], buf[96];

    if (!s_obs) { set_txt(st_live, "OBS closed"); set_tc(st_live, C_FAINT, 0); }
    else if (s_live) { fmt_hms(t, sizeof(t), s["live_s"] | 0); snprintf(buf, sizeof(buf), "LIVE %s", t);
                       set_txt(st_live, buf); set_tc(st_live, C_LIVE, 0); }
    else { set_txt(st_live, "OFF AIR"); set_tc(st_live, C_DIM, 0); }
    if (s_rec) { fmt_hms(t, sizeof(t), s["rec_s"] | 0); snprintf(buf, sizeof(buf), "%s %s", paused ? "PAUSED" : "REC", t);
                 set_txt(st_rec, buf); set_tc(st_rec, paused ? C_WARN : C_LIVE, 0); }
    else set_txt(st_rec, "");
    if (s_live) snprintf(buf, sizeof(buf), "%.1f Mbps\n%.1f%% dropped", (double)(s["bitrate"] | 0.0f), (double)(s["drop"] | 0.0f));
    else buf[0] = 0;
    set_txt(st_health, buf);

    btn_text(b_live, s_live ? LV_SYMBOL_STOP " End Stream" : LV_SYMBOL_VIDEO " Go Live");
    set_bg(b_live, s_live ? C_LIVE : lv_color_hex(0x2A0616), 0);
    btn_text(b_rec, s_rec ? LV_SYMBOL_STOP " Stop Rec" : LV_SYMBOL_STOP " Record");
    set_bg(b_rec, s_rec ? lv_color_hex(0x5A1A24) : C_CARD2, 0);
    btn_text(b_pause, paused ? LV_SYMBOL_PLAY " Resume" : LV_SYMBOL_PAUSE " Pause");
    if (s_rec) lv_obj_clear_state(b_pause, LV_STATE_DISABLED); else lv_obj_add_state(b_pause, LV_STATE_DISABLED);
    set_bg(b_clip, s_clips ? lv_color_hex(0x003A2A) : C_CARD2, 0);
    btn_text(b_mic, mic_muted ? LV_SYMBOL_MUTE " Mic muted" : LV_SYMBOL_AUDIO " Mic on");
    set_bg(b_mic, mic_muted ? lv_color_hex(0x4A0A22) : lv_color_hex(0x003A2A), 0);
    btn_text(b_cam, cam ? LV_SYMBOL_EYE_OPEN " Webcam on" : LV_SYMBOL_EYE_CLOSE " Webcam off");
    set_bg(b_cam, cam ? lv_color_hex(0x003A48) : C_CARD2, 0);

    JsonArray scenes = s["scenes"];
    String sig;
    for (const char *n : scenes) { sig += n; sig += '|'; }
    const char *cur = s["scene"] | "";
    if (sig != scenes_sig) {
        scenes_sig = sig;
        lv_obj_clean(scene_row);
        for (const char *n : scenes) {
            lv_obj_t *b = button(scene_row, n, C_CARD2, &lv_font_montserrat_20);
            lv_obj_set_height(b, 52);
            lv_obj_set_style_pad_hor(b, 18, 0);
            lv_obj_add_event_cb(b, scene_cb, LV_EVENT_CLICKED, NULL);
        }
    }
    for (uint32_t i = 0; i < lv_obj_get_child_cnt(scene_row); i++) {
        lv_obj_t *b = lv_obj_get_child(scene_row, i);
        bool on = strcmp(lv_label_get_text(lv_obj_get_child(b, 0)), cur) == 0;
        set_bg(b, on ? C_YELLOW : C_CARD2, 0);
        set_tc(lv_obj_get_child(b, 0), on ? lv_color_hex(0x04121C) : C_TEXT, 0);
    }
}

// ----------------------------------------------------------------- Mixer --
// PC tab (2026-11-13): "Volume mixer" opens a panel over the gauges - one row
// per app playing sound (Master first): name, slider, %, mute. A slider sends
// mix:<id>:<pct> when let go; the PC's next pushes are ignored for that row
// for a moment so the knob doesn't jump back while Windows catches up.
static lv_obj_t *mix_panel, *mix_list;
static String mix_sig;
struct MixRow { lv_obj_t *name, *slider, *pct, *mute; char id[40]; uint32_t hold_until; };
static MixRow mix_rows[9];
static uint8_t mix_n = 0;

static void mix_open_cb(lv_event_t *e) {
    bool open = (bool)(intptr_t)lv_event_get_user_data(e);
    show(mix_panel, open);
    if (open) lv_obj_move_foreground(mix_panel);
}

static void mix_slide_cb(lv_event_t *e) {
    MixRow *r = (MixRow *)lv_event_get_user_data(e);
    int v = lv_slider_get_value(r->slider);
    set_fmt(r->pct, "%d%%", v);
    r->hold_until = millis() + 3000;
    if (lv_event_get_code(e) == LV_EVENT_RELEASED) net_queue_action("mix:%s:%d", r->id, v);
}

static void mix_mute_cb(lv_event_t *e) {
    MixRow *r = (MixRow *)lv_event_get_user_data(e);
    net_queue_action("mix:%s:mute", r->id);
    r->hold_until = 0;
}

static void build_mixer(lv_obj_t *tab) {
    mix_panel = lv_obj_create(tab);
    lv_obj_set_size(mix_panel, lv_pct(100), lv_pct(100));
    lv_obj_align(mix_panel, LV_ALIGN_TOP_LEFT, 0, 0);
    set_bg(mix_panel, C_BG, 0);
    lv_obj_set_style_border_width(mix_panel, 0, 0);
    lv_obj_set_style_pad_all(mix_panel, 0, 0);
    lv_obj_clear_flag(mix_panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *t = label(mix_panel, &lv_font_montserrat_24, C_ACCENT, LV_SYMBOL_VOLUME_MAX " Volume mixer");
    lv_obj_align(t, LV_ALIGN_TOP_LEFT, 4, 10);
    lv_obj_t *back = button(mix_panel, LV_SYMBOL_LEFT " Back", C_CARD2, &lv_font_montserrat_20);
    lv_obj_set_size(back, 130, 44);
    lv_obj_align(back, LV_ALIGN_TOP_RIGHT, 0, 0);
    lv_obj_add_event_cb(back, mix_open_cb, LV_EVENT_CLICKED, (void *)0);
    mix_list = lv_obj_create(mix_panel);
    lv_obj_set_size(mix_list, lv_pct(100), 296);
    lv_obj_align(mix_list, LV_ALIGN_TOP_LEFT, 0, 52);
    lv_obj_set_style_bg_opa(mix_list, LV_OPA_0, 0);
    lv_obj_set_style_border_width(mix_list, 0, 0);
    lv_obj_set_style_pad_all(mix_list, 0, 0);
    lv_obj_set_style_pad_gap(mix_list, 6, 0);
    lv_obj_set_flex_flow(mix_list, LV_FLEX_FLOW_COLUMN);
    label(mix_list, &lv_font_montserrat_20, C_DIM, "Waiting for the PC...");
    lv_obj_add_flag(mix_panel, LV_OBJ_FLAG_HIDDEN);
}

static void update_mixer(JsonArray mixer) {
    String sig;
    for (JsonObject m : mixer) { sig += (const char *)(m["id"] | ""); sig += '|'; }
    if (sig != mix_sig) {
        mix_sig = sig;
        lv_obj_clean(mix_list);
        mix_n = 0;
        for (JsonObject m : mixer) {
            if (mix_n >= 9) break;
            MixRow &r = mix_rows[mix_n++];
            strlcpy(r.id, m["id"] | "", sizeof(r.id));
            r.hold_until = 0;
            lv_obj_t *c = card(mix_list);
            lv_obj_set_size(c, lv_pct(100), 62);
            lv_obj_set_style_pad_ver(c, 4, 0);
            r.name = label(c, &lv_font_montserrat_20, C_TEXT, m["name"] | "");
            lv_obj_set_width(r.name, 170);
            lv_label_set_long_mode(r.name, LV_LABEL_LONG_DOT);
            lv_obj_align(r.name, LV_ALIGN_LEFT_MID, 0, 0);
            r.slider = lv_slider_create(c);
            lv_slider_set_range(r.slider, 0, 100);
            lv_obj_set_size(r.slider, 370, 14);
            lv_obj_align(r.slider, LV_ALIGN_LEFT_MID, 190, 0);
            lv_obj_set_ext_click_area(r.slider, 18);          // easy to grab with a thumb
            set_bg(r.slider, C_CARD2, LV_PART_MAIN);
            set_bg(r.slider, C_ACCENT, LV_PART_INDICATOR);
            set_bg(r.slider, C_TEXT, LV_PART_KNOB);
            lv_obj_set_style_pad_all(r.slider, 8, LV_PART_KNOB);
            lv_obj_add_event_cb(r.slider, mix_slide_cb, LV_EVENT_VALUE_CHANGED, &r);
            lv_obj_add_event_cb(r.slider, mix_slide_cb, LV_EVENT_RELEASED, &r);
            r.pct = label(c, &lv_font_montserrat_20, C_DIM, "");
            lv_obj_align(r.pct, LV_ALIGN_LEFT_MID, 586, 0);
            r.mute = button(c, "", C_CARD2, &lv_font_montserrat_20);
            lv_obj_set_size(r.mute, 100, 46);
            lv_obj_align(r.mute, LV_ALIGN_RIGHT_MID, 0, 0);
            lv_obj_add_event_cb(r.mute, mix_mute_cb, LV_EVENT_CLICKED, &r);
        }
        if (!mix_n) label(mix_list, &lv_font_montserrat_20, C_DIM, "Nothing is playing sound");
    }
    uint8_t i = 0;
    uint32_t now = millis();
    for (JsonObject m : mixer) {
        if (i >= mix_n) break;
        MixRow &r = mix_rows[i++];
        bool muted = m["mute"] | false;
        int v = m["vol"] | 0;
        if (!lv_obj_has_state(r.slider, LV_STATE_PRESSED) && now > r.hold_until) {
            if (lv_slider_get_value(r.slider) != v) lv_slider_set_value(r.slider, v, LV_ANIM_OFF);
            set_fmt(r.pct, "%d%%", v);
        }
        set_bg(r.slider, muted ? C_FAINT : C_ACCENT, LV_PART_INDICATOR);
        btn_text(r.mute, muted ? LV_SYMBOL_MUTE : LV_SYMBOL_VOLUME_MAX);
        set_bg(r.mute, muted ? lv_color_hex(0x4A0A22) : C_CARD2, 0);
    }
}

// -------------------------------------------------------------------- PC --
struct Gauge { lv_obj_t *arc, *val, *sub; };
static Gauge g_cpu, g_gpu, g_temp, g_ram;
static lv_obj_t *pc_down, *pc_up;

static Gauge gauge(lv_obj_t *parent, const char *name) {
    Gauge g;
    lv_obj_t *c = card(parent);
    lv_obj_set_size(c, 182, 200);
    // Decorative outer ring (thin violet) - the "instrument dial" look.
    lv_obj_t *ring = lv_arc_create(c);
    lv_obj_set_size(ring, 166, 166);
    lv_obj_align(ring, LV_ALIGN_TOP_MID, 0, -12);
    lv_arc_set_rotation(ring, 135);
    lv_arc_set_bg_angles(ring, 0, 270);
    lv_arc_set_value(ring, 0);
    lv_obj_remove_style(ring, NULL, LV_PART_KNOB);
    lv_obj_clear_flag(ring, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_width(ring, 2, LV_PART_MAIN);
    lv_obj_set_style_arc_color(ring, C_VIOLET, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(ring, LV_OPA_60, LV_PART_MAIN);
    lv_obj_set_style_arc_width(ring, 0, LV_PART_INDICATOR);
    g.arc = lv_arc_create(c);
    lv_obj_set_size(g.arc, 150, 150);
    lv_obj_align(g.arc, LV_ALIGN_TOP_MID, 0, -4);
    lv_arc_set_rotation(g.arc, 135);
    lv_arc_set_bg_angles(g.arc, 0, 270);
    lv_arc_set_range(g.arc, 0, 100);
    lv_obj_remove_style(g.arc, NULL, LV_PART_KNOB);
    lv_obj_clear_flag(g.arc, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_width(g.arc, 14, LV_PART_MAIN);
    lv_obj_set_style_arc_width(g.arc, 14, LV_PART_INDICATOR);
    set_arc(g.arc, lv_color_hex(0x12182E), LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(g.arc, false, LV_PART_INDICATOR);   // squared ends: more HUD, less soft
    g.val = label(c, &lv_font_montserrat_40, C_TEXT, "-");
    lv_obj_align(g.val, LV_ALIGN_TOP_MID, 0, 50);
    lv_obj_t *n = label(c, &lv_font_montserrat_20, C_ACCENT, name);
    lv_obj_align(n, LV_ALIGN_BOTTOM_MID, 0, -18);
    g.sub = label(c, &lv_font_montserrat_14, C_YELLOW, "");
    lv_obj_align(g.sub, LV_ALIGN_BOTTOM_MID, 0, 4);
    return g;
}

static void gauge_set(Gauge &g, float v, float max, const char *fmt, float warn, float bad, const char *sub) {
    int pct = max > 0 ? (int)(v / max * 100) : 0;
    lv_arc_set_value(g.arc, pct < 0 ? 0 : pct > 100 ? 100 : pct);
    set_arc(g.arc, v >= bad ? C_BAD : v >= warn ? C_WARN : C_ACCENT, LV_PART_INDICATOR);
    set_fmt(g.val, fmt, (int)(v + 0.5f));
    set_txt(g.sub, sub);
}

static void build_pc(lv_obj_t *tab) {
    lv_obj_set_style_pad_all(tab, 10, 0);
    lv_obj_t *row = lv_obj_create(tab);
    lv_obj_set_size(row, lv_pct(100), 206);
    lv_obj_set_style_bg_opa(row, LV_OPA_0, 0);
    lv_obj_set_style_border_width(row, 0, 0);
    lv_obj_set_style_pad_all(row, 0, 0);
    lv_obj_set_style_pad_gap(row, 10, 0);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    g_cpu = gauge(row, "CPU");
    g_gpu = gauge(row, "GPU");
    g_temp = gauge(row, "GPU temp");
    g_ram = gauge(row, "Memory");

    lv_obj_t *net = card(tab);
    lv_obj_set_size(net, lv_pct(100), 120);
    lv_obj_align(net, LV_ALIGN_TOP_MID, 0, 216);
    pc_down = label(net, &lv_font_montserrat_40, lv_color_hex(0x4CC2FF), "-");
    lv_obj_align(pc_down, LV_ALIGN_LEFT_MID, 10, 0);
    pc_up = label(net, &lv_font_montserrat_40, lv_color_hex(0xB38CFF), "-");
    lv_obj_align(pc_up, LV_ALIGN_RIGHT_MID, -10, 0);
    lv_obj_t *mb = button(net, LV_SYMBOL_VOLUME_MAX " Volume mixer", lv_color_hex(0x12324A), &lv_font_montserrat_20);
    lv_obj_set_size(mb, 230, 64);
    lv_obj_align(mb, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_event_cb(mb, mix_open_cb, LV_EVENT_CLICKED, (void *)1);
    build_mixer(tab);
}

static void update_pc(JsonObject p) {
    char sub[48];
    snprintf(sub, sizeof(sub), "%.2f GHz", (double)(p["cpu_ghz"] | 0.0f));
    gauge_set(g_cpu, p["cpu"] | 0.0f, 100, "%d%%", 75, 90, sub);
    snprintf(sub, sizeof(sub), "VRAM %.1f / %.0f GB", (double)(p["vram"] | 0.0f), (double)(p["vram_total"] | 0.0f));
    gauge_set(g_gpu, p["gpu"] | 0.0f, 100, "%d%%", 75, 90, sub);
    snprintf(sub, sizeof(sub), "fan %d%%", (int)(p["fan"] | 0));
    gauge_set(g_temp, p["gpu_t"] | 0.0f, 100, "%d\xC2\xB0" "C", 72, 83, sub);
    float used = p["ram_used"] | 0.0f, total = p["ram_total"] | 1.0f;
    snprintf(sub, sizeof(sub), "%.1f / %.0f GB", (double)used, (double)total);
    gauge_set(g_ram, used / total * 100, 100, "%d%%", 80, 92, sub);
    set_fmt(pc_down, LV_SYMBOL_DOWNLOAD " %.1f Mbps", (double)(p["down"] | 0.0f));
    set_fmt(pc_up, LV_SYMBOL_UPLOAD " %.1f Mbps", (double)(p["up"] | 0.0f));
}

// ---------------------------------------------------------------- build --
static lv_obj_t *tabview;

void ui_build() {
    lv_obj_t *scr = lv_scr_act();
    set_bg(scr, C_BG, 0);
    build_header(scr);

    tabview = lv_tabview_create(scr, LV_DIR_BOTTOM, 66);
    lv_obj_set_size(tabview, SCREEN_W, SCREEN_H - 44);
    lv_obj_align(tabview, LV_ALIGN_BOTTOM_MID, 0, 0);
    set_bg(tabview, C_BG, 0);
    lv_obj_t *tb = lv_tabview_get_tab_btns(tabview);
    set_bg(tb, C_BG, 0);
    lv_obj_set_style_border_side(tb, LV_BORDER_SIDE_TOP, 0);
    lv_obj_set_style_border_width(tb, 2, 0);
    lv_obj_set_style_border_color(tb, C_ACCENT, 0);
    lv_obj_set_style_text_font(tb, &lv_font_montserrat_20, 0);
    set_tc(tb, C_DIM, 0);
    set_tc(tb, C_YELLOW, LV_PART_ITEMS | LV_STATE_CHECKED);
    lv_obj_set_style_border_color(tb, C_LIVE, LV_PART_ITEMS | LV_STATE_CHECKED);
    lv_obj_set_style_border_width(tb, 4, LV_PART_ITEMS | LV_STATE_CHECKED);
    lv_obj_set_style_bg_color(tb, lv_color_hex(0x0B0E1C), LV_PART_ITEMS | LV_STATE_CHECKED);

    build_apps(lv_tabview_add_tab(tabview, LV_SYMBOL_PLAY " Apps"));
    build_servers(lv_tabview_add_tab(tabview, LV_SYMBOL_LIST " Servers"));
    build_stream(lv_tabview_add_tab(tabview, LV_SYMBOL_VIDEO " Stream"));
    build_pc(lv_tabview_add_tab(tabview, LV_SYMBOL_SETTINGS " PC"));
    build_screen(lv_tabview_add_tab(tabview, LV_SYMBOL_IMAGE " Screen"));

    toast_lbl = label(scr, &lv_font_montserrat_24, C_TEXT, "");
    set_bg(toast_lbl, C_CARD2, 0);
    lv_obj_set_style_bg_opa(toast_lbl, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(toast_lbl, C_YELLOW, 0);
    lv_obj_set_style_border_width(toast_lbl, 2, 0);
    lv_obj_set_style_radius(toast_lbl, 3, 0);
    lv_obj_set_style_pad_all(toast_lbl, 14, 0);
    lv_obj_align(toast_lbl, LV_ALIGN_BOTTOM_MID, 0, -86);
    lv_obj_add_flag(toast_lbl, LV_OBJ_FLAG_HIDDEN);
}

// ----------------------------------------------------------------- tick --
void ui_tick() {
    static uint32_t next = 0;
    uint32_t now = millis();
    if (toast_until && now > toast_until) { lv_obj_add_flag(toast_lbl, LV_OBJ_FLAG_HIDDEN); toast_until = 0; }

    // Dim after 2 minutes untouched; full brightness on touch.
    static uint8_t bl = 80;
    uint8_t want = (now - hw_last_touch_ms() > 120000) ? 15 : 80;
    if (want != bl) { hw_backlight(want); bl = want; }

    if (now < next) return;
    next = now + 250;

    String js;
    uint32_t age;
    uint32_t seq = net_state_copy(js, &age);
    fresh = seq && age < 5000;
    if (WiFi.status() != WL_CONNECTED) set_txt(hdr_net, LV_SYMBOL_WIFI " reconnecting...");
    else if (!fresh) set_fmt(hdr_net, LV_SYMBOL_WARNING " PC not sending - %s", WiFi.localIP().toString().c_str());
    else set_txt(hdr_net, LV_SYMBOL_WIFI " PC linked");
    set_tc(hdr_net, fresh ? C_DIM : C_WARN, 0);

    // A folder tap rebuilds the Apps tab from the last state, without waiting a push.
    if (folder_dirty && !doc.isNull()) update_apps(doc["games"], doc["apps"]);
    if (!seq || seq == last_seq) return;
    last_seq = seq;
    if (deserializeJson(doc, js)) return;

    set_txt(hdr_time, doc["time"] | "");
    JsonObject s = doc["stream"];
    if (s["live"] | false) {
        char t[16];
        fmt_hms(t, sizeof(t), s["live_s"] | 0);
        set_fmt(hdr_live, LV_SYMBOL_VIDEO " LIVE %s", t);
        set_tc(hdr_live, C_LIVE, 0);
    } else {
        set_txt(hdr_live, (s["rec"] | false) ? "REC" : "OFF AIR");
        set_tc(hdr_live, (s["rec"] | false) ? C_LIVE : C_DIM, 0);
    }
    update_apps(doc["games"], doc["apps"]);
    update_screen(doc["screen"], doc["games"], doc["apps"]);
    update_servers(doc["servers"]);
    update_stream(s);
    update_pc(doc["pc"]);
    update_mixer(doc["mixer"]);
}
