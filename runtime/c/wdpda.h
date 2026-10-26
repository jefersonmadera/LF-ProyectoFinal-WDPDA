/* Esqueleto del runtime C — Semana 1 (Fase 6, versión esqueleto).
 *
 * API (la misma idea que el runtime Rust, y la que se congelará en
 * docs/ir.md cuando ambos integrantes la acuerden por escrito):
 *   wdpda_init(ctx, max_depth)   -- max_depth <= WDPDA_MAX_DEPTH
 *   wdpda_feed(ctx, bytes, n)    -- streaming por bloques
 *   wdpda_finish(ctx)            -- OK solo si la pila está vacía y
 *                                   no quedó una cadena ni una
 *                                   secuencia UTF-8 a medias.
 *
 * Qué hace este esqueleto y qué NO hace todavía (honestidad de
 * Semana 1, para que el informe no afirme de más):
 * - SÍ: pila fija sin ninguna asignación dinámica (la pila vive
 *   dentro del contexto, que el llamador reserva en su stack o en
 *   memoria estática), validación UTF-8 por AFD, equilibrio de
 *   llaves/corchetes con la pila, detección de cadenas y escapes
 *   (incluido \u con 4 hexadecimales), límite de profundidad
 *   configurable, y error con offset en bytes.
 * - NO (llega con el parser y la integración del WDPDA en la
 *   Semana 2 en adelante): validar números, literales ni la
 *   estructura completa de JSON (por ejemplo, este esqueleto acepta
 *   "{," porque solo comprueba equilibrio de delimitadores), ni
 *   validar pares sustitutos \u, ni aplicar un esquema.
 * Compilar: gcc -std=c11 -Wall -Wextra -Werror -O2
 */
#ifndef WDPDA_H
#define WDPDA_H

#include <stddef.h>
#include <stdint.h>

#define WDPDA_MAX_DEPTH 1024

typedef enum {
    WDPDA_OK = 0,
    WDPDA_ERR_DEPTH,      /* se superó max_depth */
    WDPDA_ERR_MISMATCH,   /* '}' cierra '[' o viceversa */
    WDPDA_ERR_UNBALANCED, /* finish con pila no vacía */
    WDPDA_ERR_STRING,     /* cadena sin cerrar / escape inválido */
    WDPDA_ERR_UTF8,       /* UTF-8 inválido o incompleto */
    WDPDA_ERR_CONTROL,    /* byte de control fuera de cadena */
    WDPDA_ERR_BYTE        /* byte no ASCII fuera de cadena */
} wdpda_status_t;

typedef struct {
    uint8_t stack[WDPDA_MAX_DEPTH]; /* solo '{' o '[' */
    size_t depth;
    size_t max_depth;
    size_t max_depth_seen;
    int in_string;
    int escape;          /* 1 si el byte anterior fue '\' sin resolver */
    int unicode_left;    /* hexadecimales que faltan tras \u, 0 si no */
    int utf8_left;       /* bytes de continuación que faltan */
    uint8_t utf8_lo, utf8_hi;
    uint64_t offset;     /* bytes consumidos */
    uint64_t error_offset;
    wdpda_status_t status;
} wdpda_ctx_t;

void wdpda_init(wdpda_ctx_t *ctx, size_t max_depth);
wdpda_status_t wdpda_feed(wdpda_ctx_t *ctx, const uint8_t *bytes, size_t n);
wdpda_status_t wdpda_finish(wdpda_ctx_t *ctx);
const char *wdpda_status_name(wdpda_status_t s);

#endif
