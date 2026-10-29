#include "schema.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *read_file(const char *path) {
    FILE *file = fopen(path, "rb");
    long size = 0;
    char *buffer = NULL;
    size_t read_size = 0;
    if (file == NULL) {
        return NULL;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return NULL;
    }
    size = ftell(file);
    if (size < 0) {
        fclose(file);
        return NULL;
    }
    if (fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return NULL;
    }
    buffer = (char *)malloc((size_t)size + 1);
    if (buffer == NULL) {
        fclose(file);
        return NULL;
    }
    read_size = fread(buffer, 1, (size_t)size, file);
    if (read_size != (size_t)size) {
        free(buffer);
        fclose(file);
        return NULL;
    }
    buffer[read_size] = '\0';
    fclose(file);
    return buffer;
}

int main(int argc, char **argv) {
    const char *schema_path = "avance_mvp/example_schema.json";
    char *schema_text = NULL;
    char *payload_text = NULL;
    Schema schema;
    char error[256];
    bool ok = false;

    if (argc < 2 || argc > 3) {
        fprintf(stderr, "Uso: %s <payload.json> [esquema.json]\n", argv[0]);
        fprintf(stderr, "Sin segundo argumento usa %s\n", schema_path);
        return 1;
    }
    if (argc == 3) {
        schema_path = argv[2];
    }

    schema_text = read_file(schema_path);
    if (schema_text == NULL) {
        fprintf(stderr, "No se pudo leer el esquema: %s\n", schema_path);
        return 1;
    }
    if (!parse_schema_json_error(schema_text, &schema, error,
                                 sizeof(error))) {
        fprintf(stderr, "Error al parsear el esquema JSON: %s (%s)\n",
                schema_path, error);
        free(schema_text);
        return 1;
    }

    payload_text = read_file(argv[1]);
    if (payload_text == NULL) {
        fprintf(stderr, "No se pudo leer el payload: %s\n", argv[1]);
        free(schema_text);
        return 1;
    }

    ok = validate_json_object(payload_text, &schema, error, sizeof(error));
    if (ok) {
        printf("VALIDO: %s\n", error);
    } else {
        printf("INVALIDO: %s\n", error);
    }

    free(payload_text);
    free(schema_text);
    return ok ? 0 : 2;
}
