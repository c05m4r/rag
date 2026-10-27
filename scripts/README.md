# Preparación del entorno

1. Instalar UV

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

2. Copiar las variables de entorno necesarias

    ```bash
    cp scripts/env/.tcms.conf.example scripts/env/.tcms.conf
    ```

3. Instalar dependencias

    ```bash
    cd rag-rs/scripts
    uv sync
    ```

4. Configurar las variables de entorno en scripts/env/*

# Ejecucion automatica (sync.sh)

Ejecuta Kiwi, GitLab (multiples grupos) y opcionalmente tokens leyendo todo de `scripts/.env`.

```bash
cp scripts/.env.example scripts/.env   # completar credenciales y grupos
./scripts/sync.sh list                 # ver configuracion
./scripts/sync.sh                      # kiwi + todos los grupos gitlab habilitados
./scripts/sync.sh kiwi
./scripts/sync.sh gitlab               # todos los grupos habilitados
./scripts/sync.sh gitlab core docs     # solo esos grupos
./scripts/sync.sh tokens
./scripts/sync.sh --dry-run            # muestra comandos sin ejecutarlos
```

Los grupos se definen en `GITLAB_GROUPS` y cada uno con variables `GITLAB_<GRUPO>_*` (ver `.env.example`).

# Registro de .md descargados

Kiwi y GitLab registran cada `.md` en la base SQLite `scripts/registry/registry.db`,
por fuente (`kiwi`, `gitlab_<proyecto>`):

- `items`: path, fecha de descarga, ultima actualizacion, actualizacion en origen, version y estado.
- `versions`: una fila por version con hash, fecha y contenido comprimido (gzip).

El `.md` solo se reescribe si el contenido cambio; cada cambio incrementa la version.
Si cambia el titulo, el `.md` con el nombre viejo se elimina.

```bash
./scripts/sync.sh status                     # resumen + pendientes/versionados
./scripts/sync.sh status kiwi --all          # todos los casos de Kiwi
./scripts/sync.sh status --csv registro.csv  # detalle completo en CSV
uv run md_registry.py history kiwi 100       # historial de versiones de un item
uv run md_registry.py history kiwi 100 --show 1  # contenido de la version 1
sqlite3 scripts/registry/registry.db "SELECT item_id, version, last_updated FROM items WHERE version > 1"
```

Estados:

| Estado | Significado |
|---|---|
| `descargado` | `.md` presente y sin cambios desde la ultima sync |
| `modificado_local` | el `.md` fue editado a mano despues de la sync |
| `faltante` | registrado, pero el `.md` no esta en disco |
| `sin_descargar` | existe en GitLab pero quedo fuera del filtro `STATE` |
| `eliminado_remoto` | ya no aparece en el origen en el ultimo listado completo |
| `no_registrado` | `.md` en `rag/` que ningun registro conoce |

Los `.md` descargados antes de existir el registro toman como fecha de descarga su `mtime`.

# Extraer casos de prueba Kiwi

```bash
uv run kiwi/kiwi2md.py
```

# Extraer issues de GitLab

```bash
export GITLAB_API_TOKEN="personal_access_token"
```

```bash
export GITLAB_BASE_URL="https://gitlab.com/api/v4"
```

```bash
uv run gitlab/gitlab2md.py \
  --project-id "grupo/proyecto" \
  --state "all"
```

```bash
uv run gitlab/gitlab2md.py \
  --project-id "grupo/proyecto" \
  --state "opened"
```
```bash
uv run gitlab/gitlab2md.py \
  --project-id "grupo/proyecto" \
  --base-url "https://gitlab.com/api/v4" \
  --token "<TOKEN_PERSONAL>" \
  --state "closed"
```

# Analizar tokens en rag/ para embeddings

```bash
uv run tokens/count_tokens_embeddings.py
```

Modelos personalizados:

```bash
uv run tokens/count_tokens_embeddings.py \
  --models "text-embedding-3-small,gemini-embedding-001,gemini-embedding-2"
```

Exportar detalle por archivo en CSV:

```bash
uv run tokens/count_tokens_embeddings.py \
  --csv tokens/tokens_report.csv
```
