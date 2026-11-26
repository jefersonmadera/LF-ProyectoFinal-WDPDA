/* baseline_dom.c — LÍNEA BASE del benchmark: validador basado en DOM.
 *
 * Hace lo que hacen los validadores genéricos: (1) parsea TODO el payload
 * a un árbol en memoria dinámica (malloc por nodo, realloc para los
 * hijos) y (2) recorre el árbol interpretando el esquema. Usa las MISMAS
 * tablas compiladas (wd_schema_t: AFD, constantes, nodos) que el WDPDA,
 * para que la comparación mida la arquitectura (DOM + recorrido frente a
 * una sola pasada en streaming) y no diferencias de lenguaje o de reglas.
 * Cuenta asignaciones y bytes pico.
 *
 * Cobertura: type, enum, const, minLength/maxLength, pattern, mínimos y
 * máximos, multipleOf (≤ 18 dígitos), properties, patternProperties,
 * additionalProperties, required, min/maxProperties, prefixItems, items,
 * min/maxItems, uniqueItems (O(n²)), contains, allOf/anyOf/oneOf/not,
 * if/then/else. Suficiente para los esquemas del benchmark.
 *
 * Uso: baseline <payload> [--bench R]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "wdpda_rt.h"

extern const wd_schema_t *wd_schema(void);

/* ---- asignador instrumentado -------------------------------------------- */
static size_t g_allocs, g_bytes, g_peak;

static void *xmalloc(size_t n) {
    size_t *p = (size_t *)malloc(n + sizeof(size_t));
    if (!p) { fprintf(stderr, "sin memoria\n"); exit(3); }
    *p = n;
    g_allocs++;
    g_bytes += n;
    if (g_bytes > g_peak) g_peak = g_bytes;
    return p + 1;
}

static void *xrealloc(void *q, size_t n) {
    if (!q) return xmalloc(n);
    size_t *p = (size_t *)q - 1;
    size_t old = *p;
    p = (size_t *)realloc(p, n + sizeof(size_t));
    if (!p) { fprintf(stderr, "sin memoria\n"); exit(3); }
    *p = n;
    g_allocs++;
    g_bytes += n - old;
    if (g_bytes > g_peak) g_peak = g_bytes;
    return p + 1;
}

static void xfree(void *q) {
    if (!q) return;
    size_t *p = (size_t *)q - 1;
    g_bytes -= *p;
    free(p);
}

/* ---- DOM --------------------------------------------------------------- */
enum { J_NULL, J_TRUE, J_FALSE, J_NUM, J_STR, J_ARR, J_OBJ };

typedef struct jv {
    int t;
    char *s;           /* cadena decodificada o lexema del número */
    size_t len;
    size_t ncp;        /* puntos de código (cadenas) */
    struct jv **kids;  /* elementos o valores de miembros */
    char **keys;       /* claves (objetos) */
    size_t *klen;
    size_t n, cap;
} jv;

typedef struct { const uint8_t *p; size_t i, n; int err; } parser;

static void ws(parser *P) {
    while (P->i < P->n && (P->p[P->i] == ' ' || P->p[P->i] == '\n' ||
                           P->p[P->i] == '\r' || P->p[P->i] == '\t')) P->i++;
}

static jv *newv(int t) {
    jv *v = (jv *)xmalloc(sizeof(jv));
    memset(v, 0, sizeof *v);
    v->t = t;
    return v;
}

static char *parse_str(parser *P, size_t *len, size_t *ncp) {
    size_t cap = 16, n = 0, cp = 0;
    char *b = (char *)xmalloc(cap);
    P->i++;
    while (P->i < P->n && P->p[P->i] != '"') {
        uint8_t c = P->p[P->i++];
        uint32_t u = 0;
        int emit_u = 0;
        if (c == '\\') {
            if (P->i >= P->n) { P->err = 1; break; }
            c = P->p[P->i++];
            switch (c) {
                case 'n': c = '\n'; break;
                case 't': c = '\t'; break;
                case 'r': c = '\r'; break;
                case 'b': c = '\b'; break;
                case 'f': c = '\f'; break;
                case 'u': {
                    for (int k = 0; k < 4 && P->i < P->n; k++) {
                        char h = (char)P->p[P->i++];
                        u = u * 16 + (uint32_t)(h <= '9' ? h - '0' : (h | 32) - 'a' + 10);
                    }
                    if (u >= 0xD800 && u <= 0xDBFF && P->i + 6 <= P->n) {
                        uint32_t lo = 0;
                        P->i += 2;
                        for (int k = 0; k < 4; k++) {
                            char h = (char)P->p[P->i++];
                            lo = lo * 16 + (uint32_t)(h <= '9' ? h - '0' : (h | 32) - 'a' + 10);
                        }
                        u = 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    emit_u = 1;
                    break;
                }
                default: break;
            }
        }
        uint8_t tmp[4];
        int k = 0;
        if (emit_u) {
            if (u < 0x80) tmp[k++] = (uint8_t)u;
            else if (u < 0x800) { tmp[k++] = (uint8_t)(0xC0 | (u >> 6)); tmp[k++] = (uint8_t)(0x80 | (u & 0x3F)); }
            else if (u < 0x10000) { tmp[k++] = (uint8_t)(0xE0 | (u >> 12)); tmp[k++] = (uint8_t)(0x80 | ((u >> 6) & 0x3F)); tmp[k++] = (uint8_t)(0x80 | (u & 0x3F)); }
            else { tmp[k++] = (uint8_t)(0xF0 | (u >> 18)); tmp[k++] = (uint8_t)(0x80 | ((u >> 12) & 0x3F)); tmp[k++] = (uint8_t)(0x80 | ((u >> 6) & 0x3F)); tmp[k++] = (uint8_t)(0x80 | (u & 0x3F)); }
        } else {
            tmp[k++] = c;
        }
        for (int j = 0; j < k; j++) {
            if (n + 1 >= cap) { cap *= 2; b = (char *)xrealloc(b, cap); }
            b[n++] = (char)tmp[j];
            if ((tmp[j] & 0xC0) != 0x80) cp++;
        }
    }
    if (P->i >= P->n) P->err = 1;
    P->i++;
    b[n] = 0;
    *len = n;
    *ncp = cp;
    return b;
}

static jv *parse_val(parser *P, int depth) {
    ws(P);
    if (P->i >= P->n || depth > 10000) { P->err = 1; return NULL; }
    uint8_t c = P->p[P->i];
    jv *v;
    if (c == '{' || c == '[') {
        int obj = c == '{';
        v = newv(obj ? J_OBJ : J_ARR);
        P->i++;
        ws(P);
        if (P->i < P->n && P->p[P->i] == (obj ? '}' : ']')) { P->i++; return v; }
        for (;;) {
            char *key = NULL;
            size_t kl = 0, kc;
            if (obj) {
                ws(P);
                if (P->i >= P->n || P->p[P->i] != '"') { P->err = 1; return v; }
                key = parse_str(P, &kl, &kc);
                ws(P);
                if (P->i >= P->n || P->p[P->i] != ':') { P->err = 1; return v; }
                P->i++;
            }
            jv *k = parse_val(P, depth + 1);
            if (P->err) return v;
            if (v->n == v->cap) {
                v->cap = v->cap ? v->cap * 2 : 4;
                v->kids = (jv **)xrealloc(v->kids, v->cap * sizeof(jv *));
                if (obj) {
                    v->keys = (char **)xrealloc(v->keys, v->cap * sizeof(char *));
                    v->klen = (size_t *)xrealloc(v->klen, v->cap * sizeof(size_t));
                }
            }
            if (obj) { v->keys[v->n] = key; v->klen[v->n] = kl; }
            v->kids[v->n++] = k;
            ws(P);
            if (P->i < P->n && P->p[P->i] == ',') { P->i++; continue; }
            if (P->i < P->n && P->p[P->i] == (obj ? '}' : ']')) { P->i++; return v; }
            P->err = 1;
            return v;
        }
    }
    if (c == '"') {
        v = newv(J_STR);
        v->s = parse_str(P, &v->len, &v->ncp);
        return v;
    }
    if (!strncmp((const char *)P->p + P->i, "true", 4)) { P->i += 4; return newv(J_TRUE); }
    if (!strncmp((const char *)P->p + P->i, "false", 5)) { P->i += 5; return newv(J_FALSE); }
    if (!strncmp((const char *)P->p + P->i, "null", 4)) { P->i += 4; return newv(J_NULL); }
    size_t st = P->i;
    while (P->i < P->n && strchr("+-0123456789.eE", P->p[P->i])) P->i++;
    if (P->i == st) { P->err = 1; return NULL; }
    v = newv(J_NUM);
    v->len = P->i - st;
    v->s = (char *)xmalloc(v->len + 1);
    memcpy(v->s, P->p + st, v->len);
    v->s[v->len] = 0;
    return v;
}

static void free_val(jv *v) {
    if (!v) return;
    for (size_t i = 0; i < v->n; i++) {
        free_val(v->kids[i]);
        if (v->keys) xfree(v->keys[i]);
    }
    xfree(v->kids);
    xfree(v->keys);
    xfree(v->klen);
    xfree(v->s);
    xfree(v);
}

/* ---- decimales (normalización del lexema) ------------------------------- */
typedef struct { int neg; char d[96]; int nd; long long e; } dec;

static void to_dec(const char *s, dec *o) {
    o->neg = 0; o->nd = 0; o->e = 0;
    long long frac = 0, ex = 0, zeros = 0;
    int infrac = 0, exn = 0;
    if (*s == '-') { o->neg = 1; s++; }
    for (; *s && *s != 'e' && *s != 'E'; s++) {
        if (*s == '.') { infrac = 1; continue; }
        if (infrac) frac++;
        if (*s == '0') { if (o->nd) zeros++; continue; }
        while (zeros) { if (o->nd < 95) o->d[o->nd++] = '0'; zeros--; }
        if (o->nd < 95) o->d[o->nd++] = *s;
    }
    if (*s) {
        s++;
        if (*s == '-') { exn = 1; s++; } else if (*s == '+') s++;
        for (; *s; s++) ex = ex * 10 + (*s - '0');
    }
    o->e = (exn ? -ex : ex) - frac + zeros;
    if (!o->nd) o->neg = 0;
}

static int cmp_dec(const dec *a, const wd_const_t *b) {
    if (!a->nd && !b->ndigits) return 0;
    if (!a->nd) return b->neg ? 1 : -1;
    if (!b->ndigits) return a->neg ? -1 : 1;
    if (a->neg != b->neg) return a->neg ? -1 : 1;
    long long ma = a->nd + a->e, mb = (long long)b->ndigits + b->exp;
    int r = 0;
    if (ma != mb) r = ma < mb ? -1 : 1;
    else {
        int n = a->nd > (int)b->ndigits ? a->nd : (int)b->ndigits;
        for (int i = 0; i < n && !r; i++) {
            char x = i < a->nd ? a->d[i] : '0', y = i < (int)b->ndigits ? b->digits[i] : '0';
            if (x != y) r = x < y ? -1 : 1;
        }
    }
    return a->neg ? -r : r;
}

static int mult(const dec *a, const wd_const_t *m) {
    if (!a->nd) return 1;
    long long k = a->e - m->exp;
    if (k < 0) return 0;
    unsigned long long B = 0, r = 0, p = 1;
    for (uint32_t i = 0; i < m->ndigits; i++) B = B * 10 + (unsigned)(m->digits[i] - '0');
    for (int i = 0; i < a->nd; i++) r = (r * 10 + (unsigned)(a->d[i] - '0')) % B;
    while (k-- > 0) p = (p * 10) % B;
    return (unsigned long long)((unsigned __int128)r * p % B) == 0;
}

static int32_t dfa_run(const wd_dfa_t *d, const char *s, size_t n) {
    int32_t st = 0;
    for (size_t i = 0; i < n && st >= 0; i++)
        st = d->table[(uint32_t)st * d->ncls + d->classes[(uint8_t)s[i]]];
    return st >= 0 ? d->accept[st] : -1;
}

/* ---- igualdad profunda (uniqueItems) ------------------------------------- */
static int jeq(const jv *a, const jv *b) {
    if (a->t != b->t) return 0;
    if (a->t == J_STR) return a->len == b->len && !memcmp(a->s, b->s, a->len);
    if (a->t == J_NUM) {
        dec x, y;
        to_dec(a->s, &x); to_dec(b->s, &y);
        return x.neg == y.neg && x.nd == y.nd && x.e == y.e && !memcmp(x.d, y.d, (size_t)x.nd);
    }
    if (a->t == J_ARR) {
        if (a->n != b->n) return 0;
        for (size_t i = 0; i < a->n; i++) if (!jeq(a->kids[i], b->kids[i])) return 0;
        return 1;
    }
    if (a->t == J_OBJ) {
        if (a->n != b->n) return 0;
        for (size_t i = 0; i < a->n; i++) {
            size_t j;
            for (j = 0; j < b->n; j++)
                if (a->klen[i] == b->klen[j] && !memcmp(a->keys[i], b->keys[j], a->klen[i])) break;
            if (j == b->n || !jeq(a->kids[i], b->kids[j])) return 0;
        }
        return 1;
    }
    return 1;
}

/* ---- validación interpretada sobre el DOM ------------------------------- */
static const wd_schema_t *S;

static unsigned validate(const jv *v, int32_t ni) {
    if (ni < 0) return 0;
    const wd_node_t *N = &S->nodes[ni];
    unsigned viol = 0;
    if (N->is_false) return 1;
    int jt;
    dec d;
    switch (v->t) {
        case J_NULL: jt = 1; break;
        case J_TRUE: case J_FALSE: jt = 2; break;
        case J_NUM: to_dec(v->s, &d); jt = (d.nd == 0 || d.e >= 0) ? 4 : 8; break;
        case J_STR: jt = 16; break;
        case J_ARR: jt = 32; break;
        default: jt = 64;
    }
    if (!(N->types & jt)) viol++;
    if (N->has_enum) {
        int ok = 0;
        if (v->t == J_STR) ok = N->enum_dfa >= 0 && dfa_run(&S->dfas[N->enum_dfa], v->s, v->len) >= 0;
        else if (v->t == J_NUM) for (uint32_t k = 0; k < N->enum_nnums && !ok; k++) ok = cmp_dec(&d, &S->consts[N->enum_nums[k]]) == 0;
        else if (v->t <= J_FALSE) ok = N->enum_lits & (v->t == J_TRUE ? 1 : v->t == J_FALSE ? 2 : 4);
        if (!ok) viol++;
    }
    if (v->t == J_STR && N->has_str) {
        if (N->min_len >= 0 && v->ncp < (size_t)N->min_len) viol++;
        if (N->max_len >= 0 && v->ncp > (size_t)N->max_len) viol++;
        for (uint32_t p = 0; p < N->npat; p++)
            if (dfa_run(&S->dfas[N->pats[p]], v->s, v->len) < 0) viol++;
    }
    if (v->t == J_NUM) {
        for (uint32_t k = 0; k < N->nnum; k++) {
            const wd_const_t *K = &S->consts[N->num_consts[k]];
            int c = N->num_ops[k] == WD_OP_MUL ? 0 : cmp_dec(&d, K);
            switch (N->num_ops[k]) {
                case WD_OP_GE: viol += c < 0; break;
                case WD_OP_GT: viol += c <= 0; break;
                case WD_OP_LE: viol += c > 0; break;
                case WD_OP_LT: viol += c >= 0; break;
                default: viol += !mult(&d, K);
            }
        }
    }
    if (v->t == J_OBJ && N->has_obj) {
        uint64_t seen = 0;
        for (size_t i = 0; i < v->n; i++) {
            int32_t idx = N->keys_dfa >= 0 ? dfa_run(&S->dfas[N->keys_dfa], v->keys[i], v->klen[i]) : -1;
            int declared = 0, matched = 0;
            if (idx >= 0) {
                seen |= (uint64_t)1 << idx;
                if (N->key_declared[idx]) { declared = 1; viol += validate(v->kids[i], N->key_child[idx]); }
            }
            for (uint32_t p = 0; p < N->nppat; p++)
                if (dfa_run(&S->dfas[N->ppat_dfa[p]], v->keys[i], v->klen[i]) >= 0) {
                    matched = 1;
                    viol += validate(v->kids[i], N->ppat_child[p]);
                }
            if (!declared && !matched) {
                if (N->additional == WD_FORBIDDEN) viol++;
                else viol += validate(v->kids[i], N->additional);
            }
        }
        uint64_t miss = N->required & ~seen;
        while (miss) { viol += (unsigned)(miss & 1); miss >>= 1; }
        if (N->min_props >= 0 && v->n < (size_t)N->min_props) viol++;
        if (N->max_props >= 0 && v->n > (size_t)N->max_props) viol++;
    }
    if (v->t == J_ARR && N->has_arr) {
        unsigned cont = 0;
        for (size_t i = 0; i < v->n; i++) {
            int32_t c = i < N->nprefix ? N->prefix[i] : N->items;
            if (c == WD_FORBIDDEN) viol++;
            else viol += validate(v->kids[i], c);
            if (N->contains == WD_ANY || (N->contains >= 0 && validate(v->kids[i], N->contains) == 0)) cont++;
        }
        if (N->min_items >= 0 && v->n < (size_t)N->min_items) viol++;
        if (N->max_items >= 0 && v->n > (size_t)N->max_items) viol++;
        if (N->contains != WD_NONE) {
            if (cont < (unsigned)N->min_contains) viol++;
            if (N->max_contains >= 0 && cont > (unsigned)N->max_contains) viol++;
        }
        if (N->unique)
            for (size_t i = 0; i < v->n; i++)
                for (size_t j = i + 1; j < v->n; j++)
                    if (jeq(v->kids[i], v->kids[j])) viol++;
    }
    for (uint32_t i = 0; i < N->nall; i++) viol += validate(v, N->all_of[i]);
    if (N->nany) {
        unsigned best = ~0u;
        for (uint32_t i = 0; i < N->nany; i++) { unsigned t = validate(v, N->any_of[i]); if (t < best) best = t; }
        viol += best;
    }
    if (N->none) {
        unsigned z = 0;
        for (uint32_t i = 0; i < N->none; i++) z += validate(v, N->one_of[i]) == 0;
        viol += z != 1;
    }
    if (N->not_ != WD_ANY) viol += validate(v, N->not_) == 0;
    if (N->if_ != WD_ANY) viol += validate(v, N->if_) == 0 ? validate(v, N->then_) : validate(v, N->else_);
    return viol;
}

static double now_s(void) {
    struct timespec ts;
    timespec_get(&ts, TIME_UTC);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static int cmpd(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "uso: %s <payload> [--bench R]\n", argv[0]); return 1; }
    int reps = 0;
    if (argc >= 4 && !strcmp(argv[2], "--bench")) reps = atoi(argv[3]);
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("fopen"); return 1; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *buf = (uint8_t *)malloc((size_t)sz + 1);
    if (!buf || fread(buf, 1, (size_t)sz, f) != (size_t)sz) return 1;
    fclose(f);
    S = wd_schema();
    if (reps < 1) reps = 1;
    if (reps > 101) reps = 101;
    double times[101];
    unsigned viol = 0;
    int err = 0;
    size_t allocs = 0, peak = 0;
    for (int r = -2; r < reps; r++) {
        g_allocs = g_bytes = g_peak = 0;
        double t0 = now_s();
        parser P = {buf, 0, (size_t)sz, 0};
        jv *root = parse_val(&P, 0);
        ws(&P);
        err = P.err || P.i != P.n;
        viol = err ? 0 : validate(root, S->root);
        free_val(root);
        double t1 = now_s();
        if (r >= 0) times[r] = t1 - t0;
        allocs = g_allocs;
        peak = g_peak;
    }
    qsort(times, (size_t)reps, sizeof(double), cmpd);
    double secs = times[reps / 2];
    printf("{\"status\":\"%s\",\"weight\":%u,\"bytes\":%ld,\"seconds\":%.9f,"
           "\"mb_s\":%.2f,\"allocations\":%zu,\"peak_bytes\":%zu}\n",
           err ? "MALFORMED" : (viol ? "INVALID" : "VALID"), viol, sz, secs,
           secs > 0 ? (double)sz / secs / 1e6 : 0.0, allocs, peak);
    free(buf);
    return 0;
}
