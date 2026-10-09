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
