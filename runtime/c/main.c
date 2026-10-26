/* CLI de prueba y bench del esqueleto C.
 * Uso: wdpda_cli <archivo> [tamano_bloque] [profundidad_max]
 * Lee el archivo, lo alimenta en bloques del tamaño dado y escribe
 * en stdout: OK|ERROR, bytes, profundidad máxima vista y, en caso
 * de error, código y offset. Sirve para las pruebas de equivalencia
 * por bloques y para bench/bench.py. No reserva memoria dinámica
 * para el payload: el archivo se lee por bloques en un buffer fijo.
 */
#define _POSIX_C_SOURCE 199309L
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include "wdpda.h"

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    FILE *f;
    size_t chunk = 4096, max_depth = WDPDA_MAX_DEPTH;
    uint8_t *buf;
    wdpda_ctx_t ctx;
    double t0, t1;
    size_t n;

    if (argc < 2) {
        fprintf(stderr, "uso: %s <archivo> [bloque] [profundidad_max]\n",
                argv[0]);
        return 2;
    }
    if (argc >= 3) chunk = (size_t)strtoul(argv[2], NULL, 10);
    if (argc >= 4) max_depth = (size_t)strtoul(argv[3], NULL, 10);
    if (chunk == 0) chunk = 1;
    f = fopen(argv[1], "rb");
    if (!f) { perror("fopen"); return 2; }
    buf = (uint8_t *)malloc(chunk);
    if (!buf) { fclose(f); return 2; }
    wdpda_init(&ctx, max_depth);
    t0 = now_s();
    while ((n = fread(buf, 1, chunk, f)) > 0) {
        if (wdpda_feed(&ctx, buf, n) != WDPDA_OK) break;
    }
    if (ctx.status == WDPDA_OK) wdpda_finish(&ctx);
    t1 = now_s();
    fclose(f);
    free(buf);
    printf("%s bytes=%llu depth_max=%zu",
           wdpda_status_name(ctx.status),
           (unsigned long long)ctx.offset, ctx.max_depth_seen);
    if (ctx.status != WDPDA_OK)
        printf(" error_offset=%llu", (unsigned long long)ctx.error_offset);
    printf(" seconds=%.6f\n", t1 - t0);
    return ctx.status == WDPDA_OK ? 0 : 1;
}
