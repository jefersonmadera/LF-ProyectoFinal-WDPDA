/* wdpda_rt.h — Runtime del validador WDPDA (Fases 5 y 6).
 *
 * El compilador (compiler/codegen_c.py) emite un archivo <nombre>_schema.c
 * con las tablas del autómata (nodos, AFD densos por clases de bytes,
 * constantes decimales) como datos const; este runtime las ejecuta.
 *
 * Garantías (verificadas en tests/):
 *  - Una sola pasada sobre los bytes, en streaming: wd_feed() acepta
 *    bloques de cualquier tamaño (1 byte incluido) con el mismo resultado.
 *  - Sin asignación dinámica: todo el estado vive en wd_ctx_t, cuyo
 *    tamaño es fijo en compilación (memoria O(d·B), d = profundidad
 *    máxima, B = carriles por nivel).
 *  - Sin construir un AST ni DOM: cada byte avanza AFD y la pila.
 *  - Errores con ruta JSON Pointer y posición en bytes.
 *  - API C-ABI estable (extern "C"), usable por FFI desde Rust, Python
 *    (ctypes) o compilado a WASM con emcc.
 *
 * Compilar: gcc -std=c11 -Wall -Wextra -Werror -O2
 */
#ifndef WDPDA_RT_H
#define WDPDA_RT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Límites (configurables con -D en la compilación) ---------------- */
#ifndef WD_MAX_DEPTH
#define WD_MAX_DEPTH 256      /* profundidad máxima de la pila */
#endif
#ifndef WD_MAX_LANES
#define WD_MAX_LANES 64       /* carriles del producto por nivel */
#endif
#ifndef WD_LANE_POOL
#define WD_LANE_POOL 4096     /* carriles en toda la pila */
#endif
#ifndef WD_MAX_PATS
#define WD_MAX_PATS 8         /* AFD pattern/patternProperties por carril */
#endif
#ifndef WD_MAX_ERRORS
#define WD_MAX_ERRORS 16      /* errores guardados (se cuentan todos) */
#endif
#ifndef WD_PATH_BYTES
#define WD_PATH_BYTES 1024    /* búfer del JSON Pointer actual */
#endif
#ifndef WD_ERR_PATH
#define WD_ERR_PATH 256
#endif
#ifndef WD_ERR_MSG
#define WD_ERR_MSG 160
#endif
#ifndef WD_UNIQUE_CAP
#define WD_UNIQUE_CAP 4096    /* elementos por arreglo con uniqueItems */
#endif
#ifndef WD_UNIQUE_POOL
#define WD_UNIQUE_POOL 65536  /* ranuras de hash en total (2 por elemento) */
#endif
#define WD_MAX_DIGITS 64      /* dígitos significativos guardados */

/* ---- Tablas que emite el compilador ----------------------------------- */
typedef struct {
    const uint8_t *classes;   /* byte -> clase (256 entradas) */
    uint32_t ncls;
    uint32_t nstates;
    const int32_t *table;     /* [estado * ncls + clase] -> estado, -1 muerto */
    const int32_t *accept;    /* etiqueta de aceptación o -1 */
} wd_dfa_t;

typedef struct {
    uint8_t neg;
    uint32_t ndigits;
    const char *digits;       /* sin ceros iniciales ni finales */
    int64_t exp;              /* valor = digits × 10^exp */
    const char *lexeme;
} wd_const_t;

enum { WD_OP_GE = 0, WD_OP_GT, WD_OP_LE, WD_OP_LT, WD_OP_MUL };

#define WD_ANY (-1)
#define WD_FORBIDDEN (-2)
#define WD_NONE (-3)

typedef struct {
    const char *ptr;          /* JSON Pointer del nodo en el esquema */
    uint8_t is_false;
    uint8_t types;            /* null=1 bool=2 int=4 num=8 str=16 arr=32 obj=64 */
    /* enum / const */
    uint8_t has_enum;
    int32_t enum_dfa;
    uint32_t enum_nnums;
    const uint32_t *enum_nums;
    uint8_t enum_lits;        /* true=1 false=2 null=4 */
    /* cadenas */
    uint8_t has_str;
    int32_t min_len, max_len;
    uint32_t npat;
    const uint32_t *pats;
    const char *const *pat_src;
    /* números (ordenados por costo) */
    uint32_t nnum;
    const uint8_t *num_ops;
    const uint32_t *num_consts;
    /* objetos */
    uint8_t has_obj;
    int32_t keys_dfa;
    uint32_t nkeys;
    const char *const *keys;
    const int32_t *key_child;
    const uint8_t *key_declared;
    uint64_t required;
    uint32_t nppat;
    const uint32_t *ppat_dfa;
    const int32_t *ppat_child;
    int32_t additional;
    int32_t min_props, max_props;
    uint32_t ndepreq;
    const uint8_t *depreq_key;
    const uint64_t *depreq_mask;
    uint32_t ndepsch;
    const uint8_t *depsch_key;
    const int32_t *depsch_child;
    /* arreglos */
    uint8_t has_arr;
    uint32_t nprefix;
    const int32_t *prefix;
    int32_t items;
    int32_t contains;         /* WD_NONE si no hay contains */
    int32_t min_contains, max_contains, min_items, max_items;
    uint8_t unique;
    /* combinadores */
    uint32_t nall, nany, none;
    const int32_t *all_of, *any_of, *one_of;
    int32_t not_, if_, then_, else_;
    uint8_t not_any;
    /* XML: tipo léxico del contenido (0 string, 1 boolean, 2 integer,
     * 3 decimal, 4 double); 0 en JSON */
    uint8_t xsd;
} wd_node_t;

typedef struct {
    const char *name;
    uint32_t nnodes;
    const wd_node_t *nodes;
    int32_t root;
    uint32_t ndfas;
    const wd_dfa_t *dfas;
    uint32_t nconsts;
    const wd_const_t *consts;
    uint8_t uses_unique;
    uint32_t formal_states, formal_transitions;
} wd_schema_t;

/* ---- Resultado -------------------------------------------------------- */
typedef enum {
    WD_RUNNING = -1,
    WD_VALID = 0,
    WD_INVALID,
    WD_MALFORMED,
    WD_UTF8,
    WD_DEPTH,
    WD_LIMIT,
    WD_SIZE,
    WD_TIME
} wd_status_t;

typedef enum {
    WE_TYPE = 0, WE_FALSE, WE_ENUM, WE_MIN_LENGTH, WE_MAX_LENGTH, WE_PATTERN,
    WE_MINIMUM, WE_EXCLUSIVE_MINIMUM, WE_MAXIMUM, WE_EXCLUSIVE_MAXIMUM,
    WE_MULTIPLE_OF, WE_REQUIRED, WE_ADDITIONAL, WE_DUPLICATE_KEY,
    WE_MIN_PROPERTIES, WE_MAX_PROPERTIES, WE_DEPENDENT_REQUIRED,
    WE_DEPENDENT_SCHEMAS, WE_ITEMS, WE_MIN_ITEMS, WE_MAX_ITEMS, WE_UNIQUE,
    WE_CONTAINS, WE_ANY_OF, WE_ONE_OF, WE_NOT, WE_IF_THEN, WE_IF_ELSE,
    /* XML */
    WE_XML_ROOT, WE_XML_UNDECLARED, WE_XML_CONTENT, WE_XML_INCOMPLETE,
    WE_XML_TEXT, WE_XML_ATTR, WE_XML_ATTR_REQUIRED,
    WE__COUNT
} wd_code_t;

typedef struct {
    uint8_t code;
    uint64_t offset;
    char path[WD_ERR_PATH];
    char msg[WD_ERR_MSG];
} wd_error_t;

/* ---- Estado interno (fijo) -------------------------------------------- */
typedef struct {
    int32_t node;
    int16_t parent;           /* carril padre en el mismo nivel, -1 raíz */
    int16_t owner;            /* carril dueño en el nivel inferior */
    uint8_t role, spec;
    int8_t keybit;
    uint32_t viol, acc_all;
    uint32_t any_min, one_min;
    uint16_t any_n, one_n, one_zeros;
    int32_t not_total, if_total;
    uint32_t then_total, else_total;
    uint32_t dep_add;
    uint64_t dep_fail;
    uint64_t seen;
    uint32_t count;
    int32_t pend_prop, pend_add;
    uint32_t pend_pat;
    int32_t dst[1 + WD_MAX_PATS];
    uint32_t index, contains_count;
    uint32_t uq_base, uq_n;
} wd_lane_t;

typedef struct {
    uint8_t kind;             /* 'O' | 'A' */
    uint32_t base;
    uint32_t n;
    uint32_t count;
    uint64_t hash_acc, key_hash;
    uint32_t path_start;
    uint32_t uq_top;          /* tope del pool de hashes al abrir */
} wd_level_t;

typedef struct {
    const wd_schema_t *s;
    int fail_fast;
    uint32_t max_depth;
    wd_status_t status;
    char fatal_msg[WD_ERR_MSG];
    uint64_t fatal_offset;
    uint64_t offset;          /* bytes consumidos */
    uint64_t tok_start;
    uint64_t ntokens;
    uint64_t weight;          /* peso tropical del documento */
    /* errores */
    wd_error_t errors[WD_MAX_ERRORS];
    uint32_t nerrors;
    uint64_t error_count;
    /* DPDA sintáctico */
    uint8_t syn;
    /* lexer */
    uint8_t lx;
    uint8_t str_key;
    uint8_t hex_left, sur_low;
    uint32_t hex_val, hi_sur;
    uint8_t u8_left, u8_lo, u8_hi;
    uint32_t ncp;             /* puntos de código de la cadena actual */
    uint64_t shash;           /* FNV-1a en curso de la cadena */
    /* AFD activos de la cadena en curso (se calculan al abrirla: el
     * bucle por byte solo avanza estos punteros) */
    uint32_t nact;
    const wd_dfa_t *act_d[128];
    int32_t *act_st[128];
    uint8_t lit_kind, lit_pos;
    /* número en curso */
    uint8_t nst;
    uint8_t n_neg, n_exp_neg, n_over;
    char n_digits[WD_MAX_DIGITS];
    uint32_t n_nd;            /* dígitos en el búfer */
    uint64_t n_zeros;         /* ceros pendientes (finales) */
    uint64_t n_dropped;       /* dígitos significativos tras el búfer */
    uint64_t n_frac;
    int64_t n_e;
    char n_lex[48];
    uint32_t n_lexlen;
    /* pila de niveles y carriles */
    wd_level_t levels[WD_MAX_DEPTH];
    uint32_t depth;
    uint32_t max_depth_seen;
    wd_lane_t lanes[WD_LANE_POOL];
    uint32_t lane_top;
    uint32_t lanes_peak;
    uint32_t tmp_base, tmp_n;  /* carriles del escalar en curso */
    /* JSON Pointer */
    char path[WD_PATH_BYTES];
    uint32_t path_len;
    /* uniqueItems */
    uint64_t uq[WD_UNIQUE_POOL];
    uint32_t uq_top;
    uint64_t root_hash;
} wd_ctx_t;

void wd_init(wd_ctx_t *c, const wd_schema_t *s, uint32_t max_depth,
             int fail_fast);
wd_status_t wd_feed(wd_ctx_t *c, const uint8_t *p, size_t n);
wd_status_t wd_finish(wd_ctx_t *c);
void wd_abort(wd_ctx_t *c, wd_status_t st, const char *msg);
const char *wd_status_name(wd_status_t s);
const char *wd_code_name(int code);
size_t wd_ctx_size(void);

#ifdef __cplusplus
}
#endif
#endif
