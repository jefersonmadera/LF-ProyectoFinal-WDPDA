#include "wdpda.h"

void wdpda_init(wdpda_ctx_t *ctx, size_t max_depth) {
    size_t i;
    for (i = 0; i < WDPDA_MAX_DEPTH; i++) ctx->stack[i] = 0;
    ctx->depth = 0;
    ctx->max_depth = max_depth > WDPDA_MAX_DEPTH ? WDPDA_MAX_DEPTH : max_depth;
    if (ctx->max_depth == 0) ctx->max_depth = 1;
    ctx->max_depth_seen = 0;
    ctx->in_string = 0;
    ctx->escape = 0;
    ctx->unicode_left = 0;
    ctx->utf8_left = 0;
    ctx->utf8_lo = 0x80;
    ctx->utf8_hi = 0xBF;
    ctx->offset = 0;
    ctx->error_offset = 0;
    ctx->status = WDPDA_OK;
}

const char *wdpda_status_name(wdpda_status_t s) {
    switch (s) {
        case WDPDA_OK: return "OK";
        case WDPDA_ERR_DEPTH: return "DEPTH";
        case WDPDA_ERR_MISMATCH: return "MISMATCH";
        case WDPDA_ERR_UNBALANCED: return "UNBALANCED";
        case WDPDA_ERR_STRING: return "STRING";
        case WDPDA_ERR_UTF8: return "UTF8";
        case WDPDA_ERR_CONTROL: return "CONTROL";
        case WDPDA_ERR_BYTE: return "BYTE";
        default: return "UNKNOWN";
    }
}

static wdpda_status_t fail(wdpda_ctx_t *ctx, wdpda_status_t s) {
    ctx->status = s;
    ctx->error_offset = ctx->offset;
    return s;
}

/* Un byte, ya validado como parte (o no) de UTF-8. Devuelve 0 si el
 * byte es estructuralmente válido, 1 si hay error (ya registrado). */
static int step_structural(wdpda_ctx_t *ctx, uint8_t b) {
    if (ctx->in_string) {
        if (ctx->unicode_left > 0) {
            int hex = (b >= '0' && b <= '9') || (b >= 'a' && b <= 'f') ||
                      (b >= 'A' && b <= 'F');
            if (!hex) { fail(ctx, WDPDA_ERR_STRING); return 1; }
            ctx->unicode_left--;
            return 0;
        }
        if (ctx->escape) {
            ctx->escape = 0;
            if (b == 'u') { ctx->unicode_left = 4; return 0; }
            if (b == '"' || b == '\\' || b == '/' || b == 'b' || b == 'f' ||
                b == 'n' || b == 'r' || b == 't') return 0;
            fail(ctx, WDPDA_ERR_STRING);
            return 1;
        }
        if (b == '\\') { ctx->escape = 1; return 0; }
        if (b == '"') { ctx->in_string = 0; return 0; }
        if (b < 0x20) { fail(ctx, WDPDA_ERR_CONTROL); return 1; }
        return 0;
    }
    if (b == '"') { ctx->in_string = 1; return 0; }
    if (b == '{' || b == '[') {
        if (ctx->depth >= ctx->max_depth) {
            fail(ctx, WDPDA_ERR_DEPTH);
            return 1;
        }
        ctx->stack[ctx->depth++] = b;
        if (ctx->depth > ctx->max_depth_seen)
            ctx->max_depth_seen = ctx->depth;
        return 0;
    }
    if (b == '}' || b == ']') {
        uint8_t want = (b == '}') ? '{' : '[';
        if (ctx->depth == 0 || ctx->stack[ctx->depth - 1] != want) {
            fail(ctx, WDPDA_ERR_MISMATCH);
            return 1;
        }
        ctx->depth--;
        return 0;
    }
    if (b < 0x20 && b != 0x20 && b != 0x09 && b != 0x0A && b != 0x0D) {
        /* 0x20 ya está cubierto: el resto de controles fuera de cadena
         * no son JSON válido. (El espacio, tab, LF y CR son blancos.) */
        fail(ctx, WDPDA_ERR_CONTROL);
        return 1;
    }
    return 0;
}

wdpda_status_t wdpda_feed(wdpda_ctx_t *ctx, const uint8_t *bytes, size_t n) {
    size_t i;
    if (ctx->status != WDPDA_OK) return ctx->status;
    for (i = 0; i < n; i++) {
        uint8_t b = bytes[i];
        /* AFD UTF-8 (mismo que compiler/utf8.py). Los bytes de
         * continuación no llegan a step_structural: solo el byte
         * inicial de cada carácter dispara la lógica estructural,
         * y dentro de cadena un carácter multibyte es contenido. */
        if (ctx->utf8_left > 0) {
            if (b < ctx->utf8_lo || b > ctx->utf8_hi) {
                return fail(ctx, WDPDA_ERR_UTF8);
            }
            ctx->utf8_left--;
            ctx->utf8_lo = 0x80;
            ctx->utf8_hi = 0xBF;
            ctx->offset++;
            continue;
        }
        if (b < 0x80) {
            if (step_structural(ctx, b) != 0) return ctx->status;
        } else if (!ctx->in_string) {
            /* Un carácter no ASCII fuera de cadena no es un token
             * JSON válido; se rechaza en su byte inicial. */
            return fail(ctx, WDPDA_ERR_BYTE);
        } else if (b >= 0xC2 && b <= 0xDF) {
            ctx->utf8_left = 1; ctx->utf8_lo = 0x80; ctx->utf8_hi = 0xBF;
        } else if (b == 0xE0) {
            ctx->utf8_left = 2; ctx->utf8_lo = 0xA0; ctx->utf8_hi = 0xBF;
        } else if ((b >= 0xE1 && b <= 0xEC) || b == 0xEE || b == 0xEF) {
            ctx->utf8_left = 2; ctx->utf8_lo = 0x80; ctx->utf8_hi = 0xBF;
        } else if (b == 0xED) {
            ctx->utf8_left = 2; ctx->utf8_lo = 0x80; ctx->utf8_hi = 0x9F;
        } else if (b == 0xF0) {
            ctx->utf8_left = 3; ctx->utf8_lo = 0x90; ctx->utf8_hi = 0xBF;
        } else if (b >= 0xF1 && b <= 0xF3) {
            ctx->utf8_left = 3; ctx->utf8_lo = 0x80; ctx->utf8_hi = 0xBF;
        } else if (b == 0xF4) {
            ctx->utf8_left = 3; ctx->utf8_lo = 0x80; ctx->utf8_hi = 0x8F;
        } else {
            return fail(ctx, WDPDA_ERR_UTF8);
        }
        ctx->offset++;
    }
    return WDPDA_OK;
}

wdpda_status_t wdpda_finish(wdpda_ctx_t *ctx) {
    if (ctx->status != WDPDA_OK) return ctx->status;
    if (ctx->utf8_left > 0) return fail(ctx, WDPDA_ERR_UTF8);
    if (ctx->in_string || ctx->escape || ctx->unicode_left > 0)
        return fail(ctx, WDPDA_ERR_STRING);
    if (ctx->depth != 0) return fail(ctx, WDPDA_ERR_UNBALANCED);
    return WDPDA_OK;
}
