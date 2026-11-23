/* wdpda_rt.c — Runtime del validador WDPDA: transductor léxico (AFD),
 * DPDA sintáctico dirigido por tabla y capa de esquema con carriles del
 * producto y pesos tropicales. Especificación ejecutable equivalente:
 * compiler/engine.py (las pruebas comparan ambos).
 *
 * Ninguna función de este archivo llama a malloc/free: todo el estado
 * está en wd_ctx_t.
 */
#include "wdpda_rt.h"

#include <stdio.h>
#include <string.h>

/* ---- Tokens y DPDA sintáctico (tabla densa) ---------------------------- */
enum { TK_LBRACE, TK_RBRACE, TK_LBRACK, TK_RBRACK, TK_COLON, TK_COMMA,
       TK_STRING, TK_NUMBER, TK_TRUE, TK_FALSE, TK_NULL, TK__N };
enum { S_VALUE, S_OBJ_FIRST, S_OBJ_KEY, S_OBJ_COLON, S_OBJ_NEXT, S_ARR_FIRST,
       S_ARR_NEXT, S_DONE, S__N };
enum { A_ERR, A_VALUE, A_KEY, A_COLON, A_OCOMMA, A_ACOMMA, A_OEND, A_AEND };

/* δ del DPDA sintáctico de JSON: SYN[estado][token] = acción. Es la misma
 * máquina que docs/diagramas/json_dpda.jff (JFLAP). */
static const uint8_t SYN[S__N][TK__N] = {
    /*            {        }       [        ]       :        ,
                  STR      NUM      true     false    null */
    /* VALUE  */ {A_VALUE, A_ERR,  A_VALUE, A_ERR,  A_ERR,   A_ERR,
                  A_VALUE, A_VALUE, A_VALUE, A_VALUE, A_VALUE},
    /* O_FIRST*/ {A_ERR,   A_OEND, A_ERR,   A_ERR,  A_ERR,   A_ERR,
                  A_KEY,   A_ERR,   A_ERR,   A_ERR,   A_ERR},
    /* O_KEY  */ {A_ERR,   A_ERR,  A_ERR,   A_ERR,  A_ERR,   A_ERR,
                  A_KEY,   A_ERR,   A_ERR,   A_ERR,   A_ERR},
    /* O_COLON*/ {A_ERR,   A_ERR,  A_ERR,   A_ERR,  A_COLON, A_ERR,
                  A_ERR,   A_ERR,   A_ERR,   A_ERR,   A_ERR},
    /* O_NEXT */ {A_ERR,   A_OEND, A_ERR,   A_ERR,  A_ERR,   A_OCOMMA,
                  A_ERR,   A_ERR,   A_ERR,   A_ERR,   A_ERR},
    /* A_FIRST*/ {A_VALUE, A_ERR,  A_VALUE, A_AEND, A_ERR,   A_ERR,
                  A_VALUE, A_VALUE, A_VALUE, A_VALUE, A_VALUE},
    /* A_NEXT */ {A_ERR,   A_ERR,  A_ERR,   A_AEND, A_ERR,   A_ACOMMA,
                  A_ERR,   A_ERR,   A_ERR,   A_ERR,   A_ERR},
    /* DONE   */ {A_ERR,   A_ERR,  A_ERR,   A_ERR,  A_ERR,   A_ERR,
                  A_ERR,   A_ERR,   A_ERR,   A_ERR,   A_ERR},
};

static const char *SYN_EXPECT[S__N] = {
    "se esperaba un valor", "se esperaba una clave (cadena)",
    "se esperaba una clave (cadena)", "se esperaba ':'",
    "se esperaba ',' o '}'", "se esperaba un valor", "se esperaba ',' o ']'",
    "datos después del valor JSON"};

/* Estados del lexer (AFD de tokens). */
enum { LX_WS, LX_STR, LX_ESC, LX_HEX, LX_SUR1, LX_SUR2, LX_NUM, LX_LIT,
       LX_LIT_END };
/* Estados del AFD de números. */
enum { NS_START, NS_ZERO, NS_INT, NS_DOT, NS_FRAC, NS_E, NS_ESIGN, NS_EXP };
/* Roles de carril. */
enum { R_ROOT, R_ALL, R_ANY, R_ONE, R_NOT, R_IF, R_THEN, R_ELSE, R_DEP,
       R_CONTAINS };

#define T_NULL 1
#define T_BOOL 2
#define T_INT 4
#define T_NUM 8
#define T_STR 16
#define T_ARR 32
#define T_OBJ 64

static const char *LITS[3] = {"true", "false", "null"};

/* ---- Hash canónico (mismo que compiler/engine.py) ---------------------- */
#define FNV_BASIS 0xCBF29CE484222325ULL
#define FNV_PRIME 0x100000001B3ULL

static uint64_t mix64(uint64_t x) {
    x ^= x >> 30;
    x *= 0xBF58476D1CE4E5B9ULL;
    x ^= x >> 27;
    x *= 0x94D049BB133111EBULL;
    x ^= x >> 31;
    return x;
}

/* ---- Nombres ---------------------------------------------------------- */
const char *wd_status_name(wd_status_t s) {
    switch (s) {
        case WD_RUNNING: return "RUNNING";
        case WD_VALID: return "VALID";
        case WD_INVALID: return "INVALID";
        case WD_MALFORMED: return "MALFORMED";
        case WD_UTF8: return "UTF8";
        case WD_DEPTH: return "DEPTH";
        case WD_LIMIT: return "LIMIT";
        case WD_SIZE: return "SIZE";
        case WD_TIME: return "TIME";
    }
    return "UNKNOWN";
}

static const char *CODE_NAMES[WE__COUNT] = {
    "TYPE", "FALSE", "ENUM", "MIN_LENGTH", "MAX_LENGTH", "PATTERN",
    "MINIMUM", "EXCLUSIVE_MINIMUM", "MAXIMUM", "EXCLUSIVE_MAXIMUM",
    "MULTIPLE_OF", "REQUIRED", "ADDITIONAL", "DUPLICATE_KEY",
    "MIN_PROPERTIES", "MAX_PROPERTIES", "DEPENDENT_REQUIRED",
    "DEPENDENT_SCHEMAS", "ITEMS", "MIN_ITEMS", "MAX_ITEMS", "UNIQUE",
    "CONTAINS", "ANY_OF", "ONE_OF", "NOT", "IF_THEN", "IF_ELSE",
    "XML_ROOT", "XML_UNDECLARED", "XML_CONTENT", "XML_INCOMPLETE",
    "XML_TEXT", "XML_ATTR", "XML_ATTR_REQUIRED"};

const char *wd_code_name(int code) {
    return (code >= 0 && code < WE__COUNT) ? CODE_NAMES[code] : "?";
}

size_t wd_ctx_size(void) { return sizeof(wd_ctx_t); }

/* ---- Inicio ----------------------------------------------------------- */
void wd_init(wd_ctx_t *c, const wd_schema_t *s, uint32_t max_depth,
             int fail_fast) {
    /* Solo la cabecera: la pila, los carriles, la ruta y el pool de hashes
     * se escriben antes de leerse (no hace falta borrar 1 MB por payload). */
    memset(c, 0, offsetof(wd_ctx_t, levels));
    c->depth = c->max_depth_seen = 0;
    c->lane_top = c->lanes_peak = c->tmp_base = c->tmp_n = 0;
    c->path_len = 0;
    c->uq_top = 0;
    c->root_hash = 0;
    c->s = s;
    c->fail_fast = fail_fast;
    c->max_depth = (max_depth == 0 || max_depth > WD_MAX_DEPTH)
                       ? WD_MAX_DEPTH : max_depth;
    c->status = WD_RUNNING;
    c->syn = S_VALUE;
    c->lx = LX_WS;
}

static void fatal(wd_ctx_t *c, wd_status_t st, const char *msg,
                  uint64_t off) {
    if (c->status != WD_RUNNING) return;
    c->status = st;
    c->fatal_offset = off;
    snprintf(c->fatal_msg, sizeof c->fatal_msg, "%s", msg);
}

void wd_abort(wd_ctx_t *c, wd_status_t st, const char *msg) {
    fatal(c, st, msg, c->offset);
}

#define RUNNING(c) ((c)->status == WD_RUNNING)

/* ---- Ruta JSON Pointer ------------------------------------------------- */
static void path_byte(wd_ctx_t *c, uint8_t b) {
    if (b == '~' || b == '/') {
        if (c->path_len + 2 > WD_PATH_BYTES - 1) return;
        c->path[c->path_len++] = '~';
        c->path[c->path_len++] = (b == '~') ? '0' : '1';
        return;
    }
    if (c->path_len + 1 > WD_PATH_BYTES - 1) return;
    c->path[c->path_len++] = (char)b;
}

/* Entero sin signo a decimal (sin snprintf: está en el camino crítico,
 * se llama una vez por elemento de arreglo). Devuelve la longitud. */
static int utoa10(uint64_t v, char *out) {
    char tmp[24];
    int n = 0;
    do { tmp[n++] = (char)('0' + v % 10); v /= 10; } while (v);
    for (int i = 0; i < n; i++) out[i] = tmp[n - 1 - i];
    return n;
}

static void path_index(wd_ctx_t *c, uint32_t start, uint32_t idx) {
    char tmp[24];
    tmp[0] = '/';
    int n = 1 + utoa10(idx, tmp + 1);
    c->path_len = start;
    for (int i = 0; i < n && c->path_len < WD_PATH_BYTES - 1; i++)
        c->path[c->path_len++] = tmp[i];
}

/* ---- Errores ---------------------------------------------------------- */
static void record(wd_ctx_t *c, int code, const char *msg, uint32_t plen) {
    if (!RUNNING(c)) return;
    c->error_count++;
    if (c->nerrors < WD_MAX_ERRORS) {
        wd_error_t *e = &c->errors[c->nerrors++];
        uint32_t n = plen < WD_ERR_PATH - 1 ? plen : WD_ERR_PATH - 1;
        e->code = (uint8_t)code;
        e->offset = c->tok_start;
        memcpy(e->path, c->path, n);
        e->path[n] = 0;
        snprintf(e->msg, sizeof e->msg, "%s", msg);
    }
    if (c->fail_fast) {
        if (c->weight == 0) c->weight = 1;
        c->status = WD_INVALID;
    }
}

static void violate(wd_ctx_t *c, wd_lane_t *L, int code, const char *msg,
                    uint32_t plen) {
    L->viol++;
    if (!L->spec) record(c, code, msg, plen);
}

/* ---- Aritmética decimal exacta (mismo algoritmo que numbers.py) -------- */
typedef struct {
    uint8_t neg, over;
    const char *d;
    uint32_t nd;
    int64_t e;     /* exponente del último dígito del búfer */
} dec_t;

static int cmp_dec(const dec_t *a, const wd_const_t *b) {
    int az = a->nd == 0, bz = b->ndigits == 0;
    if (az && bz) return 0;
    if (az) return b->neg ? 1 : -1;
    if (bz) return a->neg ? -1 : 1;
    if (a->neg != b->neg) return a->neg ? -1 : 1;
    int64_t ma = (int64_t)a->nd + a->e, mb = (int64_t)b->ndigits + b->exp;
    int r;
    if (ma != mb) {
        r = ma < mb ? -1 : 1;
    } else {
        uint32_t n = a->nd > b->ndigits ? a->nd : b->ndigits;
        r = 0;
        for (uint32_t i = 0; i < n && r == 0; i++) {
            char x = i < a->nd ? a->d[i] : '0';
            char y = i < b->ndigits ? b->digits[i] : '0';
            if (x != y) r = x < y ? -1 : 1;
        }
        if (r == 0 && a->over) r = 1; /* hay dígitos distintos de 0 después */
    }
    return a->neg ? -r : r;
}

static uint64_t mulmod(uint64_t a, uint64_t b, uint64_t m) {
    return (uint64_t)(((unsigned __int128)a * b) % m);
}

/* 1 múltiplo, 0 no, -1 excede el registro acotado. */
static int multiple_of(const dec_t *a, int64_t a_exp_norm,
                       const wd_const_t *m) {
    if (a->nd == 0) return 1;
    if (a->over || m->ndigits > 18) return -1;
    int64_t k = a_exp_norm - m->exp;
    if (k < 0) return 0;
    uint64_t B = 0, r = 0, p, base;
    for (uint32_t i = 0; i < m->ndigits; i++)
        B = B * 10 + (uint64_t)(m->digits[i] - '0');
    for (uint32_t i = 0; i < a->nd; i++)
        r = (r * 10 + (uint64_t)(a->d[i] - '0')) % B;
    base = 10 % B;
    p = 1 % B;
    while (k > 0) {
        if (k & 1) p = mulmod(p, base, B);
        base = mulmod(base, base, B);
        k >>= 1;
    }
    return mulmod(r, p, B) == 0;
}

/* ---- AFD ---------------------------------------------------------------- */
static inline void dfa_step(const wd_dfa_t *d, int32_t *st, uint8_t b) {
    if (*st >= 0) *st = d->table[(uint32_t)*st * d->ncls + d->classes[b]];
}

static inline int32_t dfa_tag(const wd_dfa_t *d, int32_t st) {
    return st >= 0 ? d->accept[st] : -1;
}

/* ---- Carriles ----------------------------------------------------------- */
static int add_lane(wd_ctx_t *c, uint32_t base, int32_t node, int parent,
                    int owner, int role, int spec, int keybit, int jt,
                    int is_obj_tok, int is_container, uint32_t plen) {
    if (!RUNNING(c)) return -1;
    if (c->lane_top - base >= WD_MAX_LANES || c->lane_top >= WD_LANE_POOL) {
        fatal(c, WD_LIMIT, "demasiados carriles en un nivel", c->tok_start);
        return -1;
    }
    uint32_t idx = c->lane_top - base;
    wd_lane_t *L = &c->lanes[c->lane_top++];
    if (c->lane_top > c->lanes_peak) c->lanes_peak = c->lane_top;
    memset(L, 0, sizeof *L);
    L->node = node;
    L->parent = (int16_t)parent;
    L->owner = (int16_t)owner;
    L->role = (uint8_t)role;
    L->spec = (uint8_t)spec;
    L->keybit = (int8_t)keybit;
    L->any_min = L->one_min = 0xFFFFFFFFu;
    L->not_total = L->if_total = -1;
    L->pend_prop = L->pend_add = WD_ANY;
    const wd_node_t *N = &c->s->nodes[node];
    if (N->is_false) {
        violate(c, L, WE_FALSE, "el esquema false no acepta ningún valor",
                plen);
        return 0;
    }
    if (!(N->types & jt)) {
        violate(c, L, WE_TYPE, "tipo no permitido", plen);
    } else if (N->has_enum && is_container) {
        violate(c, L, WE_ENUM, "el valor no está en enum/const", plen);
    }
    for (uint32_t i = 0; i < N->nall; i++)
        if (add_lane(c, base, N->all_of[i], (int)idx, -1, R_ALL, spec, -1, jt,
                     is_obj_tok, is_container, plen) < 0) return -1;
    for (uint32_t i = 0; i < N->nany; i++) {
        if (N->any_of[i] == WD_ANY) {
            L = &c->lanes[base + idx];
            L->any_n++;
            L->any_min = 0;
        } else if (add_lane(c, base, N->any_of[i], (int)idx, -1, R_ANY, 1, -1,
                            jt, is_obj_tok, is_container, plen) < 0) {
            return -1;
        }
    }
    for (uint32_t i = 0; i < N->none; i++) {
        if (N->one_of[i] == WD_ANY) {
            L = &c->lanes[base + idx];
            L->one_n++;
            L->one_zeros++;
            L->one_min = 0;
        } else if (add_lane(c, base, N->one_of[i], (int)idx, -1, R_ONE, 1, -1,
                            jt, is_obj_tok, is_container, plen) < 0) {
            return -1;
        }
    }
    if (N->not_ != WD_ANY &&
        add_lane(c, base, N->not_, (int)idx, -1, R_NOT, 1, -1, jt, is_obj_tok,
                 is_container, plen) < 0) return -1;
    if (N->if_ != WD_ANY) {
        if (add_lane(c, base, N->if_, (int)idx, -1, R_IF, 1, -1, jt,
                     is_obj_tok, is_container, plen) < 0) return -1;
        if (N->then_ != WD_ANY &&
            add_lane(c, base, N->then_, (int)idx, -1, R_THEN, 1, -1, jt,
                     is_obj_tok, is_container, plen) < 0) return -1;
        if (N->else_ != WD_ANY &&
            add_lane(c, base, N->else_, (int)idx, -1, R_ELSE, 1, -1, jt,
                     is_obj_tok, is_container, plen) < 0) return -1;
    }
    if (is_obj_tok && N->has_obj) {
        for (uint32_t i = 0; i < N->ndepsch; i++)
            if (N->depsch_child[i] != WD_ANY &&
                add_lane(c, base, N->depsch_child[i], (int)idx, -1, R_DEP, 1,
                         N->depsch_key[i], jt, is_obj_tok, is_container,
                         plen) < 0) return -1;
    }
    return 0;
}

typedef struct { int32_t node; int16_t owner; uint8_t role, spec; } req_t;

/* Expande los pedidos en carriles nuevos a partir de lane_top. */
static void expand(wd_ctx_t *c, const req_t *reqs, uint32_t nreq, int jt,
                   int is_obj_tok, int is_container) {
    uint32_t base = c->lane_top;
    uint32_t plen = c->path_len;
    for (uint32_t i = 0; i < nreq && RUNNING(c); i++)
        add_lane(c, base, reqs[i].node, -1, reqs[i].owner, reqs[i].role,
                 reqs[i].spec || reqs[i].role == R_CONTAINS, -1, jt,
                 is_obj_tok, is_container, plen);
}

/* Combinación tropical (⊕ = min, ⊗ = +) y entrega del peso. */
static void finalize(wd_ctx_t *c, uint32_t base, uint32_t n, uint32_t plen,
                     wd_level_t *parent) {
    char msg[WD_ERR_MSG];
    for (int i = (int)n - 1; i >= 0 && RUNNING(c); i--) {
        wd_lane_t *L = &c->lanes[base + (uint32_t)i];
        const wd_node_t *N = &c->s->nodes[L->node];
        uint64_t total = (uint64_t)L->viol + L->acc_all;
        if (N->nany) {
            uint32_t best = L->any_n ? L->any_min : 0;
            if (best > 0) {
                total += best;
                if (!L->spec) {
                    snprintf(msg, sizeof msg, "anyOf: ninguna alternativa es "
                             "válida (mejor rama: %u violaciones)", best);
                    record(c, WE_ANY_OF, msg, plen);
                }
            }
        }
        if (N->none) {
            if (L->one_zeros == 0) {
                total += L->one_n ? L->one_min : 0;
                if (!L->spec)
                    record(c, WE_ONE_OF, "oneOf: ninguna alternativa es "
                           "válida", plen);
            } else if (L->one_zeros > 1) {
                total += 1;
                if (!L->spec) {
                    snprintf(msg, sizeof msg, "oneOf: %u alternativas válidas "
                             "(debe ser exactamente una)", L->one_zeros);
                    record(c, WE_ONE_OF, msg, plen);
                }
            }
        }
        if (N->not_ != WD_ANY && L->not_total == 0) {
            total += 1;
            if (!L->spec)
                record(c, WE_NOT, "el valor no debe cumplir el esquema de not",
                       plen);
        }
        if (N->not_any) {
            total += 1;
            if (!L->spec)
                record(c, WE_NOT, "not: true no acepta ningún valor", plen);
        }
        if (N->if_ != WD_ANY) {
            if (L->if_total == 0) {
                if (N->then_ != WD_ANY && L->then_total > 0) {
                    total += L->then_total;
                    if (!L->spec)
                        record(c, WE_IF_THEN, "cumple if pero no then", plen);
                }
            } else if (N->else_ != WD_ANY && L->else_total > 0) {
                total += L->else_total;
                if (!L->spec)
                    record(c, WE_IF_ELSE, "no cumple if ni else", plen);
            }
        }
        if (L->dep_fail) {
            total += L->dep_add;
            for (int j = (int)N->ndepsch - 1; j >= 0; j--) {
                if (L->dep_fail >> N->depsch_key[j] & 1u) {
                    if (!L->spec) {
                        snprintf(msg, sizeof msg, "no cumple dependentSchemas "
                                 "de '%s'", N->keys[N->depsch_key[j]]);
                        record(c, WE_DEPENDENT_SCHEMAS, msg, plen);
                    }
                }
            }
        }
        uint32_t t = total > 0xFFFFFFFFu ? 0xFFFFFFFFu : (uint32_t)total;
        if (L->parent >= 0) {
            wd_lane_t *P = &c->lanes[base + (uint32_t)L->parent];
            switch (L->role) {
                case R_ALL: P->acc_all += t; break;
                case R_ANY:
                    P->any_n++;
                    if (t < P->any_min) P->any_min = t;
                    break;
                case R_ONE:
                    P->one_n++;
                    if (t == 0) P->one_zeros++;
                    if (t < P->one_min) P->one_min = t;
                    break;
                case R_NOT: P->not_total = (int32_t)t; break;
                case R_IF: P->if_total = (int32_t)t; break;
                case R_THEN: P->then_total = t; break;
                case R_ELSE: P->else_total = t; break;
                case R_DEP:
                    if ((P->seen >> L->keybit & 1u) && t > 0) {
                        P->dep_add += t;
                        P->dep_fail |= (uint64_t)1 << L->keybit;
                    }
                    break;
                default: break;
            }
        } else if (L->owner < 0) {
            c->weight += t;
        } else if (parent) {
            wd_lane_t *Q = &c->lanes[parent->base + (uint32_t)L->owner];
            if (L->role == R_CONTAINS) {
                if (t == 0) Q->contains_count++;
            } else {
                Q->viol += t;
            }
        }
    }
}

/* ---- uniqueItems: tabla hash abierta por carril (pool LIFO) ------------ */
#define UQ_SLOTS (2u * WD_UNIQUE_CAP)

static void after_value(wd_ctx_t *c, uint64_t h) {
    if (!RUNNING(c)) return;
    if (c->depth == 0) {
        c->syn = S_DONE;
        c->root_hash = h;
        return;
    }
    wd_level_t *lv = &c->levels[c->depth - 1];
    if (lv->kind == 'O') {
        lv->hash_acc += mix64(lv->key_hash * 3 + h);
        c->syn = S_OBJ_NEXT;
        return;
    }
    lv->hash_acc = mix64(lv->hash_acc ^ h);
    uint64_t key = h ? h : 1;
    for (uint32_t i = 0; i < lv->n && RUNNING(c); i++) {
        wd_lane_t *L = &c->lanes[lv->base + i];
        const wd_node_t *N = &c->s->nodes[L->node];
        if (!N->has_arr || !N->unique || N->is_false) continue;
        uint64_t *tab = &c->uq[L->uq_base];
        uint32_t pos = (uint32_t)(mix64(key) & (UQ_SLOTS - 1));
        int found = 0;
        while (tab[pos]) {
            if (tab[pos] == key) { found = 1; break; }
            pos = (pos + 1) & (UQ_SLOTS - 1);
        }
        if (found) {
            violate(c, L, WE_UNIQUE, "elementos repetidos (uniqueItems)",
                    c->path_len);
        } else if (L->uq_n >= WD_UNIQUE_CAP) {
            fatal(c, WD_LIMIT, "uniqueItems: el arreglo supera la capacidad "
                  "acotada", c->tok_start);
        } else {
            tab[pos] = key;
            L->uq_n++;
        }
    }
    c->syn = S_ARR_NEXT;
}

/* Calcula los pedidos de carriles para un valor que empieza. */
static uint32_t requests(wd_ctx_t *c, req_t *reqs) {
    uint32_t nr = 0;
    if (c->depth == 0) {
        if (c->s->root != WD_ANY) {
            reqs[nr].node = c->s->root;
            reqs[nr].owner = -1;
            reqs[nr].role = R_ROOT;
            reqs[nr].spec = 0;
            nr++;
        }
        return nr;
    }
    wd_level_t *lv = &c->levels[c->depth - 1];
#define PUSH_REQ(nd, ow, ro, sp)                                           \
    do {                                                                   \
        if (nr >= WD_MAX_LANES) {                                          \
            fatal(c, WD_LIMIT, "demasiados carriles en un nivel",          \
                  c->tok_start);                                           \
            return nr;                                                     \
        }                                                                  \
        reqs[nr].node = (nd); reqs[nr].owner = (int16_t)(ow);              \
        reqs[nr].role = (uint8_t)(ro); reqs[nr].spec = (uint8_t)(sp);      \
        nr++;                                                              \
    } while (0)
    if (lv->kind == 'O') {
        for (uint32_t i = 0; i < lv->n; i++) {
            wd_lane_t *L = &c->lanes[lv->base + i];
            const wd_node_t *N = &c->s->nodes[L->node];
            if (L->pend_prop >= 0) PUSH_REQ(L->pend_prop, i, R_ROOT, L->spec);
            for (uint32_t p = 0; N->has_obj && p < N->nppat; p++)
                if ((L->pend_pat >> p & 1u) && N->ppat_child[p] >= 0)
                    PUSH_REQ(N->ppat_child[p], i, R_ROOT, L->spec);
            if (L->pend_add >= 0) PUSH_REQ(L->pend_add, i, R_ROOT, L->spec);
            L->pend_prop = L->pend_add = WD_ANY;
            L->pend_pat = 0;
        }
        return nr;
    }
    path_index(c, lv->path_start, lv->count);
    for (uint32_t i = 0; i < lv->n && RUNNING(c); i++) {
        wd_lane_t *L = &c->lanes[lv->base + i];
        const wd_node_t *N = &c->s->nodes[L->node];
        if (!N->has_arr || N->is_false) continue;
        uint32_t idx = L->index++;
        int32_t child = idx < N->nprefix ? N->prefix[idx] : N->items;
        if (child == WD_FORBIDDEN) {
            char msg[WD_ERR_MSG];
            snprintf(msg, sizeof msg, "elemento %u no permitido (items: false)",
                     (unsigned)idx);
            violate(c, L, WE_ITEMS, msg, c->path_len);
        } else if (child >= 0) {
            PUSH_REQ(child, i, R_ROOT, L->spec);
        }
        if (N->contains == WD_ANY) L->contains_count++;
        else if (N->contains >= 0) PUSH_REQ(N->contains, i, R_CONTAINS, 1);
    }
    lv->count++;
#undef PUSH_REQ
    return nr;
}

static int type_of_token(wd_ctx_t *c, int tk) {
    switch (tk) {
        case TK_LBRACE: return T_OBJ;
        case TK_LBRACK: return T_ARR;
        case TK_STRING: return T_STR;
        case TK_TRUE: case TK_FALSE: return T_BOOL;
        case TK_NULL: return T_NULL;
        default: {
            /* entero si el exponente del último dígito no nulo es >= 0 */
            int64_t e = (c->n_exp_neg ? -c->n_e : c->n_e) -
                        (int64_t)c->n_frac + (int64_t)c->n_zeros;
            if (c->n_nd == 0) return T_INT;
            return e >= 0 ? T_INT : T_NUM;
        }
    }
}

/* Comienza un valor: calcula pedidos, expande carriles y, si es un
 * contenedor, apila un nivel. Para un escalar deja los carriles en
 * tmp_base / tmp_n. */
static void begin_value(wd_ctx_t *c, int tk) {
    req_t reqs[WD_MAX_LANES];
    uint32_t nr = requests(c, reqs);
    if (!RUNNING(c)) return;
    int jt = type_of_token(c, tk);
    int is_container = (tk == TK_LBRACE || tk == TK_LBRACK);
    uint32_t base = c->lane_top;
    expand(c, reqs, nr, jt, tk == TK_LBRACE, is_container);
    if (!RUNNING(c)) return;
    uint32_t n = c->lane_top - base;
    if (is_container) {
        if (c->depth + 1 > c->max_depth) {
            fatal(c, WD_DEPTH, "profundidad máxima superada", c->tok_start);
            return;
        }
        wd_level_t *lv = &c->levels[c->depth++];
        if (c->depth > c->max_depth_seen) c->max_depth_seen = c->depth;
        lv->kind = tk == TK_LBRACE ? 'O' : 'A';
        lv->base = base;
        lv->n = n;
        lv->count = 0;
        lv->hash_acc = 0;
        lv->key_hash = 0;
        lv->path_start = c->path_len;
        lv->uq_top = c->uq_top;
        if (tk == TK_LBRACK) {
            for (uint32_t i = 0; i < n; i++) {
                wd_lane_t *L = &c->lanes[base + i];
                const wd_node_t *N = &c->s->nodes[L->node];
                if (N->has_arr && N->unique && !N->is_false) {
                    if (c->uq_top + UQ_SLOTS > WD_UNIQUE_POOL) {
                        fatal(c, WD_LIMIT, "uniqueItems: sin espacio en el "
                              "pool acotado de hashes", c->tok_start);
                        return;
                    }
                    L->uq_base = c->uq_top;
                    memset(&c->uq[c->uq_top], 0, UQ_SLOTS * sizeof(uint64_t));
                    c->uq_top += UQ_SLOTS;
                }
            }
        }
        c->syn = tk == TK_LBRACE ? S_OBJ_FIRST : S_ARR_FIRST;
    } else {
        c->tmp_base = base;
        c->tmp_n = n;
    }
}

static void scalar_checks(wd_ctx_t *c, int tk) {
    char msg[WD_ERR_MSG];
    uint32_t plen = c->path_len;
    dec_t v = {0, 0, NULL, 0, 0};
    int64_t exp_norm = 0;
    if (tk == TK_NUMBER) {
        exp_norm = (c->n_exp_neg ? -c->n_e : c->n_e) - (int64_t)c->n_frac +
                   (int64_t)c->n_zeros;
        v.neg = c->n_neg;
        v.over = c->n_dropped > 0;
        v.d = c->n_digits;
        v.nd = c->n_nd;
        v.e = exp_norm + (int64_t)c->n_dropped;
        if (v.nd == 0) v.neg = 0;
    }
    for (uint32_t i = 0; i < c->tmp_n && RUNNING(c); i++) {
        wd_lane_t *L = &c->lanes[c->tmp_base + i];
        const wd_node_t *N = &c->s->nodes[L->node];
        if (N->is_false) continue;
        if (tk == TK_STRING) {
            if (N->has_str && N->min_len >= 0 &&
                c->ncp < (uint32_t)N->min_len) {
                snprintf(msg, sizeof msg, "longitud %u < minLength %d",
                         (unsigned)c->ncp, N->min_len);
                violate(c, L, WE_MIN_LENGTH, msg, plen);
            }
            if (N->has_str && N->max_len >= 0 &&
                c->ncp > (uint32_t)N->max_len) {
                snprintf(msg, sizeof msg, "longitud %u > maxLength %d",
                         (unsigned)c->ncp, N->max_len);
                violate(c, L, WE_MAX_LENGTH, msg, plen);
            }
            if (N->has_enum &&
                !(N->enum_dfa >= 0 &&
                  dfa_tag(&c->s->dfas[N->enum_dfa], L->dst[0]) >= 0))
                violate(c, L, WE_ENUM, "el valor no está en enum/const", plen);
            for (uint32_t p = 0; N->has_str && p < N->npat; p++) {
                if (dfa_tag(&c->s->dfas[N->pats[p]], L->dst[1 + p]) < 0) {
                    snprintf(msg, sizeof msg, "no coincide con pattern '%s'",
                             N->pat_src[p]);
                    violate(c, L, WE_PATTERN, msg, plen);
                }
            }
        } else if (tk == TK_NUMBER) {
            if (N->has_enum) {
                int ok = 0;
                for (uint32_t k = 0; k < N->enum_nnums && !ok; k++)
                    ok = cmp_dec(&v, &c->s->consts[N->enum_nums[k]]) == 0;
                if (!ok)
                    violate(c, L, WE_ENUM, "el valor no está en enum/const",
                            plen);
            }
            for (uint32_t k = 0; k < N->nnum; k++) {
                const wd_const_t *K = &c->s->consts[N->num_consts[k]];
                if (N->num_ops[k] == WD_OP_MUL) {
                    int r = multiple_of(&v, exp_norm, K);
                    if (r < 0) {
                        violate(c, L, WE_MULTIPLE_OF, "número con demasiados "
                                "dígitos para multipleOf (límite del registro)",
                                plen);
                    } else if (!r) {
                        snprintf(msg, sizeof msg, "no es múltiplo de %s",
                                 K->lexeme);
                        violate(c, L, WE_MULTIPLE_OF, msg, plen);
                    }
                    continue;
                }
                int cmp = cmp_dec(&v, K);
                int bad = 0, code = 0;
                const char *sym = "";
                switch (N->num_ops[k]) {
                    case WD_OP_GE: bad = cmp < 0; code = WE_MINIMUM; sym = "<";
                        break;
                    case WD_OP_GT: bad = cmp <= 0; code = WE_EXCLUSIVE_MINIMUM;
                        sym = "<="; break;
                    case WD_OP_LE: bad = cmp > 0; code = WE_MAXIMUM; sym = ">";
                        break;
                    case WD_OP_LT: bad = cmp >= 0; code = WE_EXCLUSIVE_MAXIMUM;
                        sym = ">="; break;
                    default: break;
                }
                if (bad) {
                    snprintf(msg, sizeof msg, "%.*s %s %s", (int)c->n_lexlen,
                             c->n_lex, sym, K->lexeme);
                    violate(c, L, code, msg, plen);
                }
            }
        } else if (N->has_enum) {
            int bit = tk == TK_TRUE ? 1 : (tk == TK_FALSE ? 2 : 4);
            if (!(N->enum_lits & bit))
                violate(c, L, WE_ENUM, "el valor no está en enum/const", plen);
        }
    }
}

static void end_scalar(wd_ctx_t *c, int tk, uint64_t h) {
    scalar_checks(c, tk);
    if (!RUNNING(c)) return;
    wd_level_t *parent = c->depth ? &c->levels[c->depth - 1] : NULL;
    finalize(c, c->tmp_base, c->tmp_n, c->path_len, parent);
    c->lane_top = c->tmp_base;
    c->tmp_n = 0;
    after_value(c, h);
}

static void end_container(wd_ctx_t *c) {
    char msg[WD_ERR_MSG];
    wd_level_t *lv = &c->levels[c->depth - 1];
    uint32_t plen = lv->path_start;
    for (uint32_t i = 0; i < lv->n && RUNNING(c); i++) {
        wd_lane_t *L = &c->lanes[lv->base + i];
        const wd_node_t *N = &c->s->nodes[L->node];
        if (N->is_false) continue;
        if (lv->kind == 'O' && N->has_obj) {
            uint64_t miss = N->required & ~L->seen;
            for (uint32_t b = 0; miss; b++, miss >>= 1) {
                if (miss & 1u) {
                    snprintf(msg, sizeof msg, "falta la propiedad requerida "
                             "'%s'", N->keys[b]);
                    violate(c, L, WE_REQUIRED, msg, plen);
                }
            }
            if (N->min_props >= 0 && L->count < (uint32_t)N->min_props) {
                snprintf(msg, sizeof msg, "%u propiedades < %d",
                         (unsigned)L->count, N->min_props);
                violate(c, L, WE_MIN_PROPERTIES, msg, plen);
            }
            if (N->max_props >= 0 && L->count > (uint32_t)N->max_props) {
                snprintf(msg, sizeof msg, "%u propiedades > %d",
                         (unsigned)L->count, N->max_props);
                violate(c, L, WE_MAX_PROPERTIES, msg, plen);
            }
            for (uint32_t d = 0; d < N->ndepreq; d++) {
                uint32_t kb = N->depreq_key[d];
                if (!(L->seen >> kb & 1u)) continue;
                uint64_t m = N->depreq_mask[d] & ~L->seen;
                for (uint32_t b = 0; m; b++, m >>= 1) {
                    if (m & 1u) {
                        snprintf(msg, sizeof msg, "'%s' exige '%s'",
                                 N->keys[kb], N->keys[b]);
                        violate(c, L, WE_DEPENDENT_REQUIRED, msg, plen);
                    }
                }
            }
        } else if (lv->kind == 'A' && N->has_arr) {
            if (N->min_items >= 0 && L->index < (uint32_t)N->min_items) {
                snprintf(msg, sizeof msg, "%u elementos < minItems %d",
                         (unsigned)L->index, N->min_items);
                violate(c, L, WE_MIN_ITEMS, msg, plen);
            }
            if (N->max_items >= 0 && L->index > (uint32_t)N->max_items) {
                snprintf(msg, sizeof msg, "%u elementos > maxItems %d",
                         (unsigned)L->index, N->max_items);
                violate(c, L, WE_MAX_ITEMS, msg, plen);
            }
            if (N->contains != WD_NONE) {
                if (L->contains_count < (uint32_t)N->min_contains) {
                    snprintf(msg, sizeof msg, "%u elementos cumplen contains "
                             "< %d", (unsigned)L->contains_count,
                             N->min_contains);
                    violate(c, L, WE_CONTAINS, msg, plen);
                }
                if (N->max_contains >= 0 &&
                    L->contains_count > (uint32_t)N->max_contains) {
                    snprintf(msg, sizeof msg, "%u elementos cumplen contains "
                             "> %d", (unsigned)L->contains_count,
                             N->max_contains);
                    violate(c, L, WE_CONTAINS, msg, plen);
                }
            }
        }
    }
    if (!RUNNING(c)) return;
    uint64_t h = lv->kind == 'O'
                     ? mix64(lv->hash_acc ^ mix64(5))
                     : mix64(lv->hash_acc ^ mix64(4) ^ lv->count);
    wd_level_t *parent = c->depth >= 2 ? &c->levels[c->depth - 2] : NULL;
    finalize(c, lv->base, lv->n, plen, parent);
    if (!RUNNING(c)) return;
    c->lane_top = lv->base;
    c->uq_top = lv->uq_top;
    c->path_len = lv->path_start;
    c->depth--;
    after_value(c, h);
}

/* ---- Claves ------------------------------------------------------------- */
static void act_add(wd_ctx_t *c, int32_t dfa, int32_t *st) {
    *st = 0;
    if (c->nact < 128) {
        c->act_d[c->nact] = &c->s->dfas[dfa];
        c->act_st[c->nact++] = st;
    } else {
        fatal(c, WD_LIMIT, "demasiados AFD activos en una cadena", c->tok_start);
    }
}

static void key_begin(wd_ctx_t *c) {
    wd_level_t *lv = &c->levels[c->depth - 1];
    c->path_len = lv->path_start;
    if (c->path_len < WD_PATH_BYTES - 1) c->path[c->path_len++] = '/';
    c->nact = 0;
    for (uint32_t i = 0; i < lv->n; i++) {
        wd_lane_t *L = &c->lanes[lv->base + i];
        const wd_node_t *N = &c->s->nodes[L->node];
        L->dst[0] = -1;
        for (uint32_t p = 0; p < WD_MAX_PATS; p++) L->dst[1 + p] = -1;
        if (!N->has_obj) continue;
        if (N->keys_dfa >= 0) act_add(c, N->keys_dfa, &L->dst[0]);
        for (uint32_t p = 0; p < N->nppat; p++)
            act_add(c, (int32_t)N->ppat_dfa[p], &L->dst[1 + p]);
    }
}

static void key_end(wd_ctx_t *c) {
    char msg[WD_ERR_MSG];
    wd_level_t *lv = &c->levels[c->depth - 1];
    lv->count++;
    lv->key_hash = mix64(c->shash);
    uint32_t plen = c->path_len;
    for (uint32_t i = 0; i < lv->n && RUNNING(c); i++) {
        wd_lane_t *L = &c->lanes[lv->base + i];
        const wd_node_t *N = &c->s->nodes[L->node];
        L->pend_prop = L->pend_add = WD_ANY;
        L->pend_pat = 0;
        if (N->is_false) continue;
        L->count++;
        if (!N->has_obj) continue;
        int32_t idx = N->keys_dfa >= 0
                          ? dfa_tag(&c->s->dfas[N->keys_dfa], L->dst[0]) : -1;
        int declared = 0, matched = 0;
        if (idx >= 0) {
            if (L->seen >> idx & 1u) {
                snprintf(msg, sizeof msg, "clave duplicada '%s'",
                         N->keys[idx]);
                violate(c, L, WE_DUPLICATE_KEY, msg, plen);
            }
            L->seen |= (uint64_t)1 << idx;
            if (N->key_declared[idx]) {
                declared = 1;
                if (N->key_child[idx] >= 0) L->pend_prop = N->key_child[idx];
            }
        }
        for (uint32_t p = 0; p < N->nppat; p++) {
            if (dfa_tag(&c->s->dfas[N->ppat_dfa[p]], L->dst[1 + p]) >= 0) {
                matched = 1;
                L->pend_pat |= 1u << p;
            }
        }
        if (!declared && !matched) {
            if (N->additional == WD_FORBIDDEN) {
                snprintf(msg, sizeof msg, "propiedad adicional no permitida "
                         "'%.*s'", (int)(plen - lv->path_start - 1),
                         c->path + lv->path_start + 1);
                violate(c, L, WE_ADDITIONAL, msg, plen);
            } else if (N->additional >= 0) {
                L->pend_add = N->additional;
            }
        }
    }
    c->syn = S_OBJ_COLON;
}

/* ---- Cadenas: alimentación byte a byte de los AFD ----------------------- */
static void str_begin(wd_ctx_t *c) {
    c->ncp = 0;
    c->shash = (FNV_BASIS ^ 0x53) * FNV_PRIME;
    if (c->str_key) {
        key_begin(c);
        return;
    }
    c->nact = 0;
    for (uint32_t i = 0; i < c->tmp_n; i++) {
        wd_lane_t *L = &c->lanes[c->tmp_base + i];
        const wd_node_t *N = &c->s->nodes[L->node];
        L->dst[0] = -1;
        for (uint32_t p = 0; p < WD_MAX_PATS; p++) L->dst[1 + p] = -1;
        if (N->has_enum && N->enum_dfa >= 0) act_add(c, N->enum_dfa, &L->dst[0]);
        if (N->has_str)
            for (uint32_t p = 0; p < N->npat; p++)
                act_add(c, (int32_t)N->pats[p], &L->dst[1 + p]);
    }
}

/* Un byte decodificado de la cadena: avanza solo los AFD activos. */
static inline void str_byte(wd_ctx_t *c, uint8_t b) {
    if (c->s->uses_unique) c->shash = (c->shash ^ b) * FNV_PRIME;
    if (c->str_key) path_byte(c, b);
    for (uint32_t k = 0; k < c->nact; k++) dfa_step(c->act_d[k], c->act_st[k], b);
}

/* ¿La cadena en curso necesita ver sus bytes? (si no, se salta rápido) */
static inline int str_needs_bytes(const wd_ctx_t *c) {
    return c->nact || c->str_key || c->s->uses_unique;
}

static void str_cp(wd_ctx_t *c, uint32_t cp) {
    uint8_t buf[4];
    int n;
    if (cp < 0x80) { buf[0] = (uint8_t)cp; n = 1; }
    else if (cp < 0x800) {
        buf[0] = (uint8_t)(0xC0 | (cp >> 6));
        buf[1] = (uint8_t)(0x80 | (cp & 0x3F)); n = 2;
    } else if (cp < 0x10000) {
        buf[0] = (uint8_t)(0xE0 | (cp >> 12));
        buf[1] = (uint8_t)(0x80 | ((cp >> 6) & 0x3F));
        buf[2] = (uint8_t)(0x80 | (cp & 0x3F)); n = 3;
    } else {
        buf[0] = (uint8_t)(0xF0 | (cp >> 18));
        buf[1] = (uint8_t)(0x80 | ((cp >> 12) & 0x3F));
        buf[2] = (uint8_t)(0x80 | ((cp >> 6) & 0x3F));
        buf[3] = (uint8_t)(0x80 | (cp & 0x3F)); n = 4;
    }
    for (int i = 0; i < n; i++) str_byte(c, buf[i]);
    c->ncp++;
}

static void str_end(wd_ctx_t *c) {
    if (c->str_key) {
        key_end(c);
        return;
    }
    end_scalar(c, TK_STRING, mix64(c->shash));
}

/* ---- Números -------------------------------------------------------------- */
static void num_sig(wd_ctx_t *c, char d) {
    if (c->n_nd < WD_MAX_DIGITS && c->n_dropped == 0)
        c->n_digits[c->n_nd++] = d;
    else
        c->n_dropped++;
}

static void num_digit(wd_ctx_t *c, uint8_t b, int frac) {
    if (frac) c->n_frac++;
    if (b == '0') {
        if (c->n_nd == 0 && c->n_dropped == 0) return; /* cero inicial */
        c->n_zeros++;
        return;
    }
    while (c->n_zeros) {
        num_sig(c, '0');
        c->n_zeros--;
    }
    num_sig(c, (char)b);
}

static void num_begin(wd_ctx_t *c, uint8_t b) {
    c->n_neg = (b == '-');
    c->n_exp_neg = 0;
    c->n_nd = 0;
    c->n_zeros = 0;
    c->n_dropped = 0;
    c->n_frac = 0;
    c->n_e = 0;
    c->n_lexlen = 0;
    if (b == '-') c->nst = NS_START;
    else if (b == '0') { c->nst = NS_ZERO; num_digit(c, b, 0); }
    else { c->nst = NS_INT; num_digit(c, b, 0); }
}

/* 1 = consumido, 0 = fin del número (byte a reprocesar), -1 = error. */
static int num_byte(wd_ctx_t *c, uint8_t b) {
    int dig = b >= '0' && b <= '9';
    switch (c->nst) {
        case NS_START:
            if (b == '0') { c->nst = NS_ZERO; num_digit(c, b, 0); return 1; }
            if (dig) { c->nst = NS_INT; num_digit(c, b, 0); return 1; }
            return -1;
        case NS_ZERO:
            if (dig) return -1;
            if (b == '.') { c->nst = NS_DOT; return 1; }
            if (b == 'e' || b == 'E') { c->nst = NS_E; return 1; }
            return 0;
        case NS_INT:
            if (dig) { num_digit(c, b, 0); return 1; }
            if (b == '.') { c->nst = NS_DOT; return 1; }
            if (b == 'e' || b == 'E') { c->nst = NS_E; return 1; }
            return 0;
        case NS_DOT:
            if (dig) { c->nst = NS_FRAC; num_digit(c, b, 1); return 1; }
            return -1;
        case NS_FRAC:
            if (dig) { num_digit(c, b, 1); return 1; }
            if (b == 'e' || b == 'E') { c->nst = NS_E; return 1; }
            return 0;
        case NS_E:
            if (b == '+' || b == '-') {
                c->n_exp_neg = (b == '-');
                c->nst = NS_ESIGN;
                return 1;
            }
            if (dig) { c->nst = NS_EXP; c->n_e = b - '0'; return 1; }
            return -1;
        case NS_ESIGN:
            if (dig) { c->nst = NS_EXP; c->n_e = b - '0'; return 1; }
            return -1;
        case NS_EXP:
            if (dig) {
                if (c->n_e < 1000000000000000LL) c->n_e = c->n_e * 10 + (b - '0');
                return 1;
            }
            return 0;
    }
    return -1;
}

static int num_accepting(const wd_ctx_t *c) {
    return c->nst == NS_ZERO || c->nst == NS_INT || c->nst == NS_FRAC ||
           c->nst == NS_EXP;
}

static uint64_t num_hash(wd_ctx_t *c) {
    if (!c->s->uses_unique) return 0; /* el hash solo sirve a uniqueItems */
    uint64_t h = (FNV_BASIS ^ 0x4E) * FNV_PRIME;
    if (c->n_nd == 0) {
        h = (h ^ '0') * FNV_PRIME;
        return mix64(h);
    }
    if (c->n_neg) h = (h ^ '-') * FNV_PRIME;
    for (uint32_t i = 0; i < c->n_nd; i++)
        h = (h ^ (uint8_t)c->n_digits[i]) * FNV_PRIME;
    int64_t e = (c->n_exp_neg ? -c->n_e : c->n_e) - (int64_t)c->n_frac +
                (int64_t)c->n_zeros;
    char tmp[32];
    int n = 0;
    tmp[n++] = 'e';
    if (e < 0) { tmp[n++] = '-'; n += utoa10((uint64_t)(-e), tmp + n); }
    else n += utoa10((uint64_t)e, tmp + n);
    for (int i = 0; i < n; i++) h = (h ^ (uint8_t)tmp[i]) * FNV_PRIME;
    return mix64(h);
}

/* ---- Despacho de tokens al DPDA sintáctico ------------------------------ */
static void syn_token(wd_ctx_t *c, int tk) {
    c->ntokens++;
    uint8_t a = SYN[c->syn][tk];
    switch (a) {
        case A_ERR:
            fatal(c, WD_MALFORMED, SYN_EXPECT[c->syn], c->tok_start);
            return;
        case A_VALUE:
            begin_value(c, tk);
            if (!RUNNING(c)) return;
            if (tk == TK_STRING) {
                c->str_key = 0;
            } else if (tk == TK_NUMBER) {
                end_scalar(c, tk, num_hash(c));
            } else if (tk == TK_TRUE || tk == TK_FALSE || tk == TK_NULL) {
                end_scalar(c, tk, tk == TK_TRUE ? mix64(2)
                                  : tk == TK_FALSE ? mix64(3) : mix64(1));
            }
            return;
        case A_KEY: c->str_key = 1; return;
        case A_COLON: c->syn = S_VALUE; return;
        case A_OCOMMA: c->syn = S_OBJ_KEY; return;
        case A_ACOMMA: c->syn = S_VALUE; return;
        case A_OEND: case A_AEND: end_container(c); return;
    }
}

static int is_ws(uint8_t b) {
    return b == ' ' || b == '\t' || b == '\n' || b == '\r';
}

static int is_punct(uint8_t b) {
    return b == '{' || b == '}' || b == '[' || b == ']' || b == ':' ||
           b == ',';
}

static int punct_tok(uint8_t b) {
    switch (b) {
        case '{': return TK_LBRACE;
        case '}': return TK_RBRACE;
        case '[': return TK_LBRACK;
        case ']': return TK_RBRACK;
        case ':': return TK_COLON;
        default: return TK_COMMA;
    }
}

static int hexval(uint8_t b) {
    if (b >= '0' && b <= '9') return b - '0';
    if (b >= 'a' && b <= 'f') return b - 'a' + 10;
    if (b >= 'A' && b <= 'F') return b - 'A' + 10;
    return -1;
}

static int utf8_lead(wd_ctx_t *c, uint8_t b) {
    if (b >= 0xC2 && b <= 0xDF) { c->u8_left = 1; c->u8_lo = 0x80; c->u8_hi = 0xBF; }
    else if (b == 0xE0) { c->u8_left = 2; c->u8_lo = 0xA0; c->u8_hi = 0xBF; }
    else if ((b >= 0xE1 && b <= 0xEC) || b == 0xEE || b == 0xEF) {
        c->u8_left = 2; c->u8_lo = 0x80; c->u8_hi = 0xBF;
    } else if (b == 0xED) { c->u8_left = 2; c->u8_lo = 0x80; c->u8_hi = 0x9F; }
    else if (b == 0xF0) { c->u8_left = 3; c->u8_lo = 0x90; c->u8_hi = 0xBF; }
    else if (b >= 0xF1 && b <= 0xF3) { c->u8_left = 3; c->u8_lo = 0x80; c->u8_hi = 0xBF; }
    else if (b == 0xF4) { c->u8_left = 3; c->u8_lo = 0x80; c->u8_hi = 0x8F; }
    else return -1;
    return 0;
}

/* Un byte fuera de token: inicia el token que corresponda. */
static void start_token(wd_ctx_t *c, uint8_t b) {
    if (is_ws(b)) return;
    c->tok_start = c->offset;
    if (b == '"') {
        syn_token(c, TK_STRING);
        if (!RUNNING(c)) return;
        str_begin(c);
        c->lx = LX_STR;
    } else if (is_punct(b)) {
        syn_token(c, punct_tok(b));
    } else if (b == '-' || (b >= '0' && b <= '9')) {
        num_begin(c, b);
        if (c->n_lexlen < sizeof c->n_lex) c->n_lex[c->n_lexlen++] = (char)b;
        c->lx = LX_NUM;
    } else if (b == 't' || b == 'f' || b == 'n') {
        c->lit_kind = b == 't' ? 0 : (b == 'f' ? 1 : 2);
        c->lit_pos = 1;
        c->lx = LX_LIT;
    } else if (b >= 0x80) {
        if (utf8_lead(c, b) < 0) fatal(c, WD_UTF8, "byte UTF-8 inválido", c->offset);
        else fatal(c, WD_MALFORMED, "byte inesperado fuera de cadena", c->offset);
    } else {
        fatal(c, WD_MALFORMED, "byte inesperado", c->offset);
    }
}

static void end_number(wd_ctx_t *c) {
    if (!num_accepting(c)) {
        fatal(c, WD_MALFORMED, "número incompleto", c->tok_start);
        return;
    }
    syn_token(c, TK_NUMBER);
}

static void end_literal(wd_ctx_t *c) {
    syn_token(c, TK_TRUE + c->lit_kind);
}

wd_status_t wd_feed(wd_ctx_t *c, const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n && RUNNING(c); i++, c->offset++) {
        uint8_t b = p[i];
        switch (c->lx) {
            case LX_WS:
                start_token(c, b);
                break;
            case LX_STR:
                if (c->u8_left) {
                    if (b < c->u8_lo || b > c->u8_hi) {
                        fatal(c, WD_UTF8, "byte de continuación UTF-8 inválido",
                              c->offset);
                        break;
                    }
                    c->u8_left--;
                    c->u8_lo = 0x80;
                    c->u8_hi = 0xBF;
                    str_byte(c, b);
                } else if (b == '"') {
                    c->lx = LX_WS;
                    str_end(c);
                } else if (b == '\\') {
                    c->lx = LX_ESC;
                } else if (b < 0x20) {
                    fatal(c, WD_MALFORMED, "carácter de control sin escapar",
                          c->offset);
                } else if (b < 0x80) {
                    /* Camino rápido: tramo de ASCII imprimible sin '"' ni
                     * '\\' (la gran mayoría de los bytes de una cadena). */
                    size_t j = i;
                    if (str_needs_bytes(c)) {
                        while (j < n && p[j] >= 0x20 && p[j] < 0x80 &&
                               p[j] != '"' && p[j] != '\\')
                            str_byte(c, p[j++]);
                    } else {
                        while (j < n && p[j] >= 0x20 && p[j] < 0x80 &&
                               p[j] != '"' && p[j] != '\\')
                            j++;
                    }
                    c->ncp += (uint32_t)(j - i);
                    c->offset += (uint64_t)(j - i - 1);
                    i = j - 1;
                } else if (utf8_lead(c, b) < 0) {
                    fatal(c, WD_UTF8, "byte UTF-8 inválido", c->offset);
                } else {
                    str_byte(c, b);
                    c->ncp++;
                }
                break;
            case LX_ESC: {
                uint8_t v = 0;
                switch (b) {
                    case '"': v = '"'; break;
                    case '\\': v = '\\'; break;
                    case '/': v = '/'; break;
                    case 'b': v = '\b'; break;
                    case 'f': v = '\f'; break;
                    case 'n': v = '\n'; break;
                    case 'r': v = '\r'; break;
                    case 't': v = '\t'; break;
                    case 'u':
                        c->hex_left = 4;
                        c->hex_val = 0;
                        c->sur_low = 0;
                        c->lx = LX_HEX;
                        break;
                    default:
                        fatal(c, WD_MALFORMED, "escape inválido", c->offset);
                }
                if (b != 'u' && RUNNING(c)) {
                    str_byte(c, v);
                    c->ncp++;
                    c->lx = LX_STR;
                }
                break;
            }
            case LX_HEX: {
                int h = hexval(b);
                if (h < 0) {
                    fatal(c, WD_MALFORMED, "escape \\u no hexadecimal", c->offset);
                    break;
                }
                c->hex_val = c->hex_val * 16 + (uint32_t)h;
                if (--c->hex_left) break;
                uint32_t cp = c->hex_val;
                if (c->sur_low) {
                    if (cp < 0xDC00 || cp > 0xDFFF) {
                        fatal(c, WD_MALFORMED, "sustituto alto sin sustituto "
                              "bajo", c->offset);
                        break;
                    }
                    str_cp(c, 0x10000 + ((c->hi_sur - 0xD800) << 10) +
                                  (cp - 0xDC00));
                    c->sur_low = 0;
                    c->lx = LX_STR;
                } else if (cp >= 0xD800 && cp <= 0xDBFF) {
                    c->hi_sur = cp;
                    c->lx = LX_SUR1;
                } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
                    fatal(c, WD_MALFORMED, "sustituto bajo suelto", c->offset);
                } else {
                    str_cp(c, cp);
                    c->lx = LX_STR;
                }
                break;
            }
            case LX_SUR1:
                if (b == '\\') c->lx = LX_SUR2;
                else fatal(c, WD_MALFORMED, "sustituto alto sin pareja", c->offset);
                break;
            case LX_SUR2:
                if (b == 'u') {
                    c->hex_left = 4;
                    c->hex_val = 0;
                    c->sur_low = 1;
                    c->lx = LX_HEX;
                } else {
                    fatal(c, WD_MALFORMED, "sustituto alto sin pareja", c->offset);
                }
                break;
            case LX_NUM: {
                int r = num_byte(c, b);
                if (r > 0) {
                    if (c->n_lexlen < sizeof c->n_lex)
                        c->n_lex[c->n_lexlen++] = (char)b;
                    break;
                }
                if (r < 0) {
                    fatal(c, WD_MALFORMED, "número inválido", c->offset);
                    break;
                }
                c->lx = LX_WS;
                end_number(c);
                if (RUNNING(c)) start_token(c, b);
                break;
            }
            case LX_LIT:
                if (b == (uint8_t)LITS[c->lit_kind][c->lit_pos]) {
                    c->lit_pos++;
                    if (LITS[c->lit_kind][c->lit_pos] == 0) c->lx = LX_LIT_END;
                } else {
                    fatal(c, WD_MALFORMED, "literal inválido", c->tok_start);
                }
                break;
            case LX_LIT_END:
                if (is_ws(b) || is_punct(b)) {
                    c->lx = LX_WS;
                    end_literal(c);
                    if (RUNNING(c)) start_token(c, b);
                } else {
                    fatal(c, WD_MALFORMED, "literal inválido", c->tok_start);
                }
                break;
        }
    }
    return c->status;
}

wd_status_t wd_finish(wd_ctx_t *c) {
    if (!RUNNING(c)) return c->status;
    switch (c->lx) {
        case LX_NUM: c->lx = LX_WS; end_number(c); break;
        case LX_LIT_END: c->lx = LX_WS; end_literal(c); break;
        case LX_LIT: fatal(c, WD_MALFORMED, "literal incompleto", c->tok_start);
            break;
        case LX_WS: break;
        default:
            if (c->u8_left) fatal(c, WD_UTF8, "secuencia UTF-8 incompleta", c->offset);
            else fatal(c, WD_MALFORMED, "cadena sin cerrar", c->tok_start);
    }
    if (RUNNING(c) && c->syn != S_DONE)
        fatal(c, WD_MALFORMED, "entrada incompleta: el valor JSON no terminó",
              c->offset);
    if (RUNNING(c)) c->status = c->weight == 0 ? WD_VALID : WD_INVALID;
    return c->status;
}
