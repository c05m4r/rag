# Exportador de Issues de GitLab a Markdown

Script para exportar issues de GitLab a formato Markdown, incluyendo comentarios y relaciones.

## Requisitos

- [uv](https://docs.astral.sh/uv/) instalado
- Token de API de GitLab con permisos `api` o `read_api`
- Acceso al proyecto en GitLab

## Uso

### Exportar todos los issues de un proyecto

```bash
cd /home/c05m4r/git/rag/scripts/gitlab

uv run --with=rich,requests,python-dotenv gitlab2md.py \
  --base-url https://gitlab.example.com \
  --project-id "mi-org/mi-proyecto" \
  --token "$GITLAB_API_TOKEN" \
  --state all
```

### Exportar un único issue

```bash
cd /home/c05m4r/git/rag/scripts/gitlab

uv run --with=rich,requests,python-dotenv gitlab2md.py \
  --base-url https://gitlab.example.com \
  --project-id "mi-org/mi-proyecto" \
  --token "$GITLAB_API_TOKEN" \
  --issue-iid 123
```

### Usando variables de entorno

```bash
export GITLAB_BASE_URL="https://gitlab.example.com"
export GITLAB_PROJECT_ID="mi-org/mi-proyecto"
export GITLAB_API_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
export GITLAB_ISSUE_STATE="all"

cd /home/c05m4r/git/rag/scripts/gitlab
uv run --with=rich,requests,python-dotenv gitlab2md.py
```

## Parámetros

| Parámetro | Requerido | Default | Descripción |
|---|---|---|---|
| `--base-url` | No | `https://gitlab.com` | URL base de GitLab |
| `--project-id` | Sí | - | Path del proyecto (ej.: `siu-arai/docs-api`) |
| `--token` | Sí | - | Personal Access Token (scope `api` o `read_api`) |
| `--state` | No | `all` | Estado: `opened`, `closed` o `all` |
| `--issue-iid` | No | - | Exporta solo este IID |
| `--output-dir` | No | `../../rag/gitlab` | Directorio de salida |

## Archivos generados

Formato: `<project-prefix>_issue_<iid>_<titulo-sanitizado>.md`

Ejemplo: `mi_org_mi_proyecto_issue_123_ejemplo_de_issue.md`

Cada archivo incluye: metadatos, descripción, relaciones y comentarios.
