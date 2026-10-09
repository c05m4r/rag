"""Orquestador de scripts de extraccion (Kiwi, GitLab, tokens).

Lee la configuracion de scripts/.env y ejecuta los scripts existentes
como subprocesos, sin modificar su forma de trabajo.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

SCRIPTS_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPTS_DIR / ".env"

KIWI_SCRIPT = SCRIPTS_DIR / "kiwi" / "kiwi2md.py"
GITLAB_SCRIPT = SCRIPTS_DIR / "gitlab" / "gitlab2md.py"
TOKENS_SCRIPT = SCRIPTS_DIR / "tokens" / "count_tokens_embeddings.py"

TRUE_VALUES = {"1", "true", "yes", "si", "on"}


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(
            f"No existe {path}. Copiar scripts/.env.example a scripts/.env y completarlo."
        )
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def is_true(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in TRUE_VALUES


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]


def group_key(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()


def run(cmd: list[str], extra_env: dict[str, str], dry_run: bool) -> bool:
    printable = " ".join(cmd)
    print(f"\n$ {printable}", flush=True)
    if dry_run:
        return True
    env = {**os.environ, **extra_env}
    result = subprocess.run(cmd, env=env, cwd=SCRIPTS_DIR)
    if result.returncode != 0:
        print(f"[ERROR] Fallo (exit {result.returncode}): {printable}", file=sys.stderr)
        return False
    return True


def run_kiwi(env: dict[str, str], dry_run: bool) -> bool:
    print("\n=== Kiwi TCMS ===")
    extra_env = {
        key: env[key]
        for key in ("TCMS_URL", "TCMS_USERNAME", "TCMS_PASSWORD")
        if env.get(key)
    }
    return run([sys.executable, str(KIWI_SCRIPT)], extra_env, dry_run)


def gitlab_groups(env: dict[str, str]) -> list[dict]:
    groups = []
    for name in split_list(env.get("GITLAB_GROUPS")):
        prefix = f"GITLAB_{group_key(name)}_"

        def get(field: str, default: str | None = None) -> str | None:
            return env.get(prefix + field) or default

        groups.append(
            {
                "name": name,
                "enabled": is_true(get("ENABLED"), default=True),
                "project_id": get("PROJECT_ID"),
                "state": get("STATE", env.get("GITLAB_ISSUE_STATE", "all")),
                "issues": split_list(get("ISSUES")),
                "output_dir": get("OUTPUT_DIR", env.get("GITLAB_OUTPUT_DIR")),
                "base_url": get("BASE_URL", env.get("GITLAB_BASE_URL")),
                "token": get("TOKEN", env.get("GITLAB_API_TOKEN")),
                "prefix": prefix,
            }
        )
    return groups


def run_gitlab(env: dict[str, str], selected: list[str], dry_run: bool) -> bool:
    print("\n=== GitLab ===")
    groups = gitlab_groups(env)
    if not groups:
        print("Sin grupos definidos (GITLAB_GROUPS vacio).")
        return True

    if selected:
        wanted = {group_key(name) for name in selected}
        unknown = wanted - {group_key(g["name"]) for g in groups}
        if unknown:
            raise SystemExit(f"Grupos no definidos en GITLAB_GROUPS: {', '.join(sorted(unknown))}")
        groups = [g for g in groups if group_key(g["name"]) in wanted]
    else:
        groups = [g for g in groups if g["enabled"]]

    ok = True
    for group in groups:
        print(f"\n--- Grupo: {group['name']} ---")
        if not group["project_id"]:
            print(f"[ERROR] Falta {group['prefix']}PROJECT_ID", file=sys.stderr)
            ok = False
            continue
        if not group["token"]:
            print(
                f"[ERROR] Falta GITLAB_API_TOKEN o {group['prefix']}TOKEN",
                file=sys.stderr,
            )
            ok = False
            continue

        # Token y URL por entorno para no exponerlos en la linea de comandos.
        extra_env = {"GITLAB_API_TOKEN": group["token"]}
        if group["base_url"]:
            extra_env["GITLAB_BASE_URL"] = group["base_url"]

        base_cmd = [
            sys.executable,
            str(GITLAB_SCRIPT),
            "--project-id",
            group["project_id"],
        ]
        if group["output_dir"]:
            base_cmd += ["--output-dir", group["output_dir"]]

        if group["issues"]:
            for iid in group["issues"]:
                ok = run(base_cmd + ["--issue-iid", iid], extra_env, dry_run) and ok
        else:
            ok = run(base_cmd + ["--state", group["state"]], extra_env, dry_run) and ok
    return ok


def run_tokens(env: dict[str, str], dry_run: bool) -> bool:
    print("\n=== Tokens ===")
    cmd = [sys.executable, str(TOKENS_SCRIPT)]
    if env.get("TOKENS_MODELS"):
        cmd += ["--models", env["TOKENS_MODELS"]]
    if env.get("TOKENS_CSV"):
        cmd += ["--csv", env["TOKENS_CSV"]]
    return run(cmd, {}, dry_run)


def list_config(env: dict[str, str], env_file: Path) -> None:
    print(f"Config: {env_file}")
    print(f"Kiwi:   {'habilitado' if is_true(env.get('KIWI_ENABLED'), True) else 'deshabilitado'}")
    print(f"Tokens: {'habilitado' if is_true(env.get('TOKENS_ENABLED')) else 'deshabilitado'}")
    print("Grupos GitLab:")
    for g in gitlab_groups(env):
        target = f"issues {', '.join(g['issues'])}" if g["issues"] else f"state={g['state']}"
        status = "" if g["enabled"] else " (deshabilitado)"
        print(f"  - {g['name']}: {g['project_id'] or '<sin PROJECT_ID>'} [{target}]{status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta los scripts de extraccion usando scripts/.env",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=["all", "kiwi", "gitlab", "tokens", "list"],
        help="Que ejecutar (default: all)",
    )
    parser.add_argument(
        "groups",
        nargs="*",
        help="Solo con 'gitlab': grupos a ejecutar (default: todos los habilitados)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra los comandos sin ejecutarlos",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_FILE),
        help="Archivo .env alternativo",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env_file = Path(args.env_file).resolve()
    env = load_env(env_file)

    if args.groups and args.target != "gitlab":
        raise SystemExit("Solo se pueden indicar grupos con el target 'gitlab'")

    if args.target == "list":
        list_config(env, env_file)
        return

    ok = True
    if args.target == "kiwi" or (
        args.target == "all" and is_true(env.get("KIWI_ENABLED"), True)
    ):
        ok = run_kiwi(env, args.dry_run) and ok
    if args.target in ("gitlab", "all"):
        ok = run_gitlab(env, args.groups, args.dry_run) and ok
    if args.target == "tokens" or (
        args.target == "all" and is_true(env.get("TOKENS_ENABLED"))
    ):
        ok = run_tokens(env, args.dry_run) and ok

    if not ok:
        raise SystemExit(1)
    print("\nListo.")


if __name__ == "__main__":
    main()
