/* wdpda_cli.c — CLI del validador generado.
 *
 * Uso: validator <payload|-> [--chunk N] [--max-depth D] [--fail-fast]
 *                [--max-bytes N] [--timeout-ms T] [--bench R]
 *
 * Imprime una línea JSON con el resultado y las métricas:
 *   status, weight (número de violaciones; null = mal formado),
 *   errors[] (código, ruta JSON Pointer, byte, mensaje), bytes,
 *   max_depth, lanes_peak, tokens, seconds, mb_s, cycles_per_byte,
 *   runtime_allocations (siempre 0: el runtime no llama a malloc),
 *   ctx_bytes (memoria fija del validador), automaton (estados y
 *   transiciones de δ).
 *
 * En modo normal el archivo se lee por bloques en un búfer estático (el
 * validador nunca ve el payload completo). En --bench R el CLI carga el
 * payload en memoria una vez (fuera de la medición) y repite R veces
 * init/feed/finish, reportando la mediana.
 * Códigos de salida: 0 válido, 2 inválido, 3 mal formado/límite, 1 uso.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "wdpda_rt.h"

/* El mismo CLI sirve para JSON y para XML (compilado con -DWD_XML). */
#ifdef WD_XML
#include "wdpda_xml.h"
typedef wx_ctx_t ctx_t;
typedef wx_schema_t schema_t;
#define V_INIT wx_init
#define V_FEED wx_feed
#define V_FINISH wx_finish
#define V_ABORT wx_abort
#define V_CTX_SIZE wx_ctx_size
#define V_SCHEMA wx_schema
#define V_NODES(s) ((s)->nelems)
#else
typedef wd_ctx_t ctx_t;
typedef wd_schema_t schema_t;
#define V_INIT wd_init
#define V_FEED wd_feed
#define V_FINISH wd_finish
#define V_ABORT wd_abort
#define V_CTX_SIZE wd_ctx_size
#define V_SCHEMA wd_schema
#define V_NODES(s) ((s)->nnodes)
#endif

#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__)
#include <x86intrin.h>
#define HAVE_RDTSC 1
static uint64_t cycles(void) { return __rdtsc(); }
#else
#define HAVE_RDTSC 0
static uint64_t cycles(void) { return 0; }
#endif

extern const schema_t *V_SCHEMA(void);

static ctx_t CTX;              /* estático: sin malloc para el validador */
static uint8_t BUF[1 << 16];

static double now_s(void) {
    struct timespec ts;
    timespec_get(&ts, TIME_UTC);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static void json_str(const char *s) {
    putchar('"');
    for (; *s; s++) {
        unsigned char ch = (unsigned char)*s;
        if (ch == '"' || ch == '\\') { putchar('\\'); putchar(ch); }
        else if (ch < 0x20) printf("\\u%04x", ch);
        else putchar(ch);
    }
    putchar('"');
}

static int cmpd(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static void print_result(const ctx_t *c, double secs, uint64_t cyc,
                         const schema_t *s);

/* Modo por lotes: una línea = un payload; una línea JSON de salida por
 * cada una (lo usan las pruebas cruzadas Python/C/Rust). */
static int run_jsonl(FILE *f, const schema_t *s, size_t chunk,
                     uint32_t max_depth, int fail_fast) {
    static uint8_t line[1 << 20];
    size_t len = 0;
    int ch;
    for (;;) {
        ch = fgetc(f);
        if (ch == '\n' || ch == EOF) {
            if (len > 0 || ch == '\n') {
                if (len && line[len - 1] == '\r') len--;
                V_INIT(&CTX, s, max_depth, fail_fast);
                for (size_t off = 0; off < len && CTX.status == WD_RUNNING;
                     off += chunk)
                    V_FEED(&CTX, line + off,
                            len - off < chunk ? len - off : chunk);
                V_FINISH(&CTX);
                print_result(&CTX, 0, 0, s);
            }
            len = 0;
            if (ch == EOF) break;
            continue;
        }
        if (len < sizeof line) line[len++] = (uint8_t)ch;
    }
    return 0;
}

int main(int argc, char **argv) {
    const char *file = NULL;
    size_t chunk = sizeof BUF;
    uint32_t max_depth = 0;
    int fail_fast = 0, bench = 0, jsonl = 0;
    unsigned long long max_bytes = 0, timeout_ms = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--chunk") && i + 1 < argc)
            chunk = (size_t)strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--max-depth") && i + 1 < argc)
            max_depth = (uint32_t)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--fail-fast")) fail_fast = 1;
        else if (!strcmp(argv[i], "--max-bytes") && i + 1 < argc)
            max_bytes = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--timeout-ms") && i + 1 < argc)
            timeout_ms = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--bench") && i + 1 < argc)
            bench = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--jsonl")) jsonl = 1;
        else if (!file) file = argv[i];
        else { fprintf(stderr, "argumento desconocido: %s\n", argv[i]); return 1; }
    }
    if (!file) {
        fprintf(stderr, "uso: %s <payload|-> [--chunk N] [--max-depth D] "
                "[--fail-fast] [--max-bytes N] [--timeout-ms T] [--bench R]\n",
                argv[0]);
        return 1;
    }
    if (chunk == 0) chunk = 1;
    if (chunk > sizeof BUF) chunk = sizeof BUF;
    const schema_t *s = V_SCHEMA();
    FILE *f = strcmp(file, "-") ? fopen(file, "rb") : stdin;
    if (!f) { perror("fopen"); return 1; }
    if (jsonl) {
        int r = run_jsonl(f, s, chunk, max_depth, fail_fast);
        if (f != stdin) fclose(f);
        return r;
    }

    double secs = 0;
    uint64_t cyc = 0;
    if (bench > 0) {
        /* Carga única fuera de la medición (asignación del CLI, no del
         * validador). */
        size_t cap = 1 << 20, len = 0, n;
        uint8_t *all = (uint8_t *)malloc(cap);
        if (!all) return 1;
        while ((n = fread(all + len, 1, cap - len, f)) > 0) {
            len += n;
            if (len == cap) {
                uint8_t *p = (uint8_t *)realloc(all, cap * 2);
                if (!p) return 1;
                all = p;
                cap *= 2;
            }
        }
        if (bench > 101) bench = 101;
        double times[101];
        uint64_t cy[101];
        for (int r = -2; r < bench; r++) { /* 2 de calentamiento */
            double t0 = now_s();
            uint64_t c0 = cycles();
            V_INIT(&CTX, s, max_depth, fail_fast);
            for (size_t off = 0; off < len && CTX.status == WD_RUNNING;
                 off += chunk)
                V_FEED(&CTX, all + off, len - off < chunk ? len - off : chunk);
            V_FINISH(&CTX);
            uint64_t c1 = cycles();
            double t1 = now_s();
            if (r >= 0) { times[r] = t1 - t0; cy[r] = c1 - c0; }
        }
        double sorted[101];
        memcpy(sorted, times, sizeof(double) * (size_t)bench);
        qsort(sorted, (size_t)bench, sizeof(double), cmpd);
        secs = sorted[bench / 2];
        for (int r = 0; r < bench; r++)
            if (times[r] == secs) { cyc = cy[r]; break; }
        free(all);
    } else {
        size_t n;
        V_INIT(&CTX, s, max_depth, fail_fast);
        double t0 = now_s(), deadline = t0 + (double)timeout_ms / 1000.0;
        uint64_t c0 = cycles();
        while (CTX.status == WD_RUNNING && (n = fread(BUF, 1, chunk, f)) > 0) {
            if (max_bytes && CTX.offset + n > max_bytes) {
                V_ABORT(&CTX, WD_SIZE, "el payload supera el tamaño máximo");
                break;
            }
            V_FEED(&CTX, BUF, n);
            if (timeout_ms && now_s() > deadline) {
                V_ABORT(&CTX, WD_TIME, "se superó el tiempo límite");
                break;
            }
        }
        V_FINISH(&CTX);
        cyc = cycles() - c0;
        secs = now_s() - t0;
    }
    if (f != stdin) fclose(f);
    print_result(&CTX, secs, cyc, s);
    if (CTX.status == WD_VALID) return 0;
    if (CTX.status == WD_INVALID) return 2;
    return 3;
}

static void print_result(const ctx_t *c, double secs, uint64_t cyc,
                         const schema_t *s) {
    int fatal = c->status != WD_VALID && c->status != WD_INVALID;
    printf("{\"status\":\"%s\",\"weight\":", wd_status_name(c->status));
    if (fatal) printf("null"); else printf("%llu", (unsigned long long)c->weight);
    printf(",\"errors\":[");
    for (uint32_t i = 0; i < c->nerrors; i++) {
        const wd_error_t *e = &c->errors[i];
        printf("%s{\"code\":\"%s\",\"path\":", i ? "," : "", wd_code_name(e->code));
        json_str(e->path);
        printf(",\"offset\":%llu,\"message\":", (unsigned long long)e->offset);
        json_str(e->msg);
        putchar('}');
    }
    printf("],\"error_count\":%llu", (unsigned long long)c->error_count);
    if (fatal) {
        printf(",\"fatal_message\":");
        json_str(c->fatal_msg);
        printf(",\"fatal_offset\":%llu", (unsigned long long)c->fatal_offset);
    }
    double mbs = secs > 0 ? (double)c->offset / secs / 1e6 : 0;
    printf(",\"bytes\":%llu,\"max_depth\":%u,\"lanes_peak\":%u,\"tokens\":%llu,"
           "\"seconds\":%.9f,\"mb_s\":%.2f,",
           (unsigned long long)c->offset, c->max_depth_seen, c->lanes_peak,
           (unsigned long long)c->ntokens, secs, mbs);
    if (HAVE_RDTSC && c->offset)
        printf("\"cycles_per_byte\":%.2f,", (double)cyc / (double)c->offset);
    else
        printf("\"cycles_per_byte\":null,");
    printf("\"runtime_allocations\":0,\"ctx_bytes\":%llu,\"automaton\":{"
           "\"states\":%u,\"transitions\":%u,\"nodes\":%u,\"dfas\":%u}}\n",
           (unsigned long long)V_CTX_SIZE(), s->formal_states,
           s->formal_transitions, V_NODES(s), s->ndfas);
}
