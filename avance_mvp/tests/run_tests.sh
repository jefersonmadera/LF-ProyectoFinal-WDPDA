#!/usr/bin/env bash
# Pruebas del validador corregido (avance_mvp).
# Uso: desde la raíz del repo:  bash avance_mvp/tests/run_tests.sh
# Compila con el estándar del proyecto (-Werror) y corre 15 casos.
# Sale con 0 solo si todos pasan; cada caso revisa código de salida
# y un fragmento de la salida, no solo que "no se cayó".
set -u
cd "$(dirname "$0")/../.." || exit 1

CC=${CC:-gcc}
BIN=/tmp/validator_mvp_test
PASS=0
FAIL=0

echo "== compilación =="
if ! $CC -std=c11 -Wall -Wextra -Werror -O2 -o "$BIN" \
    avance_mvp/validator.c avance_mvp/schema.c; then
  echo "FALLO: no compila limpio con -Werror"
  exit 1
fi
echo "OK: compila limpio con -Wall -Wextra -Werror"

check() {
  # check <nombre> <código-esperado> <fragmento-esperado> <json>
  local name="$1" want_code="$2" want_text="$3" json="$4"
  local tmp
  tmp=$(mktemp)
  printf '%s' "$json" > "$tmp"
  local out code
  out=$("$BIN" "$tmp" avance_mvp/example_schema.json 2>&1)
  code=$?
  rm -f "$tmp"
  if [ "$code" = "$want_code" ] && [[ "$out" == *"$want_text"* ]]; then
    echo "OK:   $name -> $out"
    PASS=$((PASS + 1))
  else
    echo "FALLO: $name -> código $code, salida: $out (esperaba $want_code / $want_text)"
    FAIL=$((FAIL + 1))
  fi
}

echo "== casos =="
check "válido con formato bonito (ejemplo del repo)" 0 "VALIDO: OK" "$(cat avance_mvp/example_payload_valid.json)"
check "válido minificado" 0 "VALIDO: OK" '{"edad": 25, "nombre": "Ana", "activo": true}'
check "válido con espacios y saltos extra" 0 "VALIDO: OK" '{
  "edad"   :   25 ,
  "nombre" : "Ana"
}'
check "inválido del ejemplo (edad es string)" 2 "expected integer but found string" "$(cat avance_mvp/example_payload_invalid.json)"
check "falta campo requerido nombre" 2 "Missing required field: nombre" '{"edad": 25, "activo": true}'
check "edad con decimales (25.5) no es entero" 2 "expected integer but found float" '{"edad": 25.5, "nombre": "Ana", "activo": true}'
check "25.0 sí cuenta como entero (JSON Schema)" 0 "VALIDO: OK" '{"edad": 25.0, "nombre": "Ana", "activo": true}'
check "malformado: valor sin comillas ni número" 2 "expected a JSON value" '{"edad": abc, "nombre": "Ana", "activo": true}'
check "malformado: basura después del objeto" 2 "trailing data" '{"edad": 25, "nombre": "Ana"} basura'
check "clave dentro de un string no engaña" 2 "Missing required field: edad" '{"nota": "la \"edad\": 99 es alta", "nombre": "Ana"}'
check "booleano inválido (maybe no es true/false)" 2 "expected a JSON value" '{"edad": 25, "nombre": "Ana", "activo": maybe}'
check "literal pegado a letras (truely no es true)" 2 "invalid literal" '{"edad": 25, "nombre": "Ana", "activo": truely}'
check "campo extra se permite (sin additionalProperties)" 0 "VALIDO: OK" '{"edad": 25, "nombre": "Ana", "activo": true, "extra": [1, 2]}'
check "campo anidado con tipo equivocado" 2 "expected integer" '{"edad": {"años": 25}, "nombre": "Ana"}'
check "clave duplicada en el objeto" 2 "duplicate field" '{"edad": 25, "edad": 26, "nombre": "Ana"}'

echo "== resultado: $PASS pasaron, $FAIL fallaron =="
[ "$FAIL" = "0" ]
