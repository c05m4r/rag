import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


def sanitize_filename(value: str) -> str:
    value = value.strip()
    replacements = str.maketrans(
        {
            "ñ": "n",
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "Ñ": "n",
            "Á": "a",
            "É": "e",
            "Í": "i",
            "Ó": "o",
            "Ú": "u",
        }
    )
    value = value.translate(replacements).lower()
    value = re.sub(r'[\\/<>:?"\'*|]+', "_", value)
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_. ") or "unnamed"


def request_json(url: str, token: str) -> tuple[Any, dict[str, str]]:
    request = Request(
        url,
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
            "User-Agent": "gitlab2md/1.0",
        },
    )
    with urlopen(request) as response:
        payload = response.read().decode("utf-8")
        headers = dict(response.headers.items())
    return json.loads(payload), headers


def paged_get(
    base_url: str, api_path: str, token: str, per_page: int = 100
) -> list[dict]:
    page = 1
    results: list[dict] = []
    while True:
        separator = "&" if "?" in api_path else "?"
        url = f"{base_url}{api_path}{separator}per_page={per_page}&page={page}"
        data, headers = request_json(url, token)
        if not isinstance(data, list):
            break
        results.extend(data)

        next_page = headers.get("X-Next-Page", "").strip()
        if not next_page:
            break
        page = int(next_page)

    return results


def normalize_api_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/api/v4"):
        return base_url
    return f"{base_url}/api/v4"


def encode_project_id(project_id: str) -> str:
    return quote(project_id, safe="")


def fetch_issues(base_url: str, project_id: str, token: str, state: str) -> list[dict]:
    encoded = encode_project_id(project_id)
    path = f"/projects/{encoded}/issues?scope=all&state={state}&order_by=created_at&sort=asc"
    return paged_get(base_url, path, token)


def fetch_issue_notes(
    base_url: str, project_id: str, issue_iid: int, token: str
) -> list[dict]:
    encoded = encode_project_id(project_id)
    path = f"/projects/{encoded}/issues/{issue_iid}/notes?sort=asc"
    return paged_get(base_url, path, token)


def fetch_issue_links(
    base_url: str, project_id: str, issue_iid: int, token: str
) -> list[dict]:
    encoded = encode_project_id(project_id)
    path = f"/projects/{encoded}/issues/{issue_iid}/links"
    return paged_get(base_url, path, token)


def format_issue_markdown(issue: dict, notes: list[dict], links: list[dict]) -> str:
    title = issue.get("title") or "Untitled"
    lines = [f"# {title}", ""]

    lines.extend(
        [
            f"- **Issue ID**: {issue.get('id')}",
            f"- **Issue IID**: {issue.get('iid')}",
            f"- **Estado**: {issue.get('state')}",
            f"- **Autor**: {(issue.get('author') or {}).get('name', 'N/A')}",
            f"- **Creado**: {issue.get('created_at')}",
            f"- **Actualizado**: {issue.get('updated_at')}",
            f"- **Web URL**: {issue.get('web_url')}",
        ]
    )

    labels = issue.get("labels") or []
    if labels:
        lines.append(f"- **Labels**: {', '.join(labels)}")

    assignees = issue.get("assignees") or []
    if assignees:
        names = [a.get("name", "") for a in assignees if a.get("name")]
        if names:
            lines.append(f"- **Assignees**: {', '.join(names)}")

    lines.append("")

    description = (issue.get("description") or "").strip()
    lines.extend(["## Descripcion", "", description or "(Sin descripcion)", ""])

    lines.extend(["## Relaciones con otros issues", ""])
    if links:
        for link in links:
            related = link.get("target_issue") or link.get("source_issue") or {}
            rel_ref = (
                related.get("references", {}).get("full")
                or f"#{related.get('iid', 'N/A')}"
            )
            rel_title = related.get("title", "Sin titulo")
            rel_url = related.get("web_url", "")
            rel_state = related.get("state", "unknown")
            if rel_url:
                lines.append(f"- {rel_ref} [{rel_state}] - {rel_title} ({rel_url})")
            else:
                lines.append(f"- {rel_ref} [{rel_state}] - {rel_title}")
    else:
        lines.append("- Sin relaciones registradas")
    lines.append("")

    lines.extend(["## Comentarios", ""])
    if notes:
        for note in notes:
            author_name = (note.get("author") or {}).get("name", "N/A")
            created = note.get("created_at", "")
            body = (note.get("body") or "").strip() or "(Comentario vacio)"
            system_tag = " [system]" if note.get("system") else ""
            lines.extend(
                [
                    f"### {author_name}{system_tag} - {created}",
                    "",
                    body,
                    "",
                ]
            )
    else:
        lines.append("- Sin comentarios")

    return "\n".join(lines).strip() + "\n"


def save_issue_markdown(
    issue: dict, content: str, output_dir: Path, project_prefix: str
) -> None:
    iid = issue.get("iid")
    title = issue.get("title") or "unnamed"
    filename = f"{project_prefix}_issue_{iid}_{sanitize_filename(title)}.md"
    path = output_dir / filename
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exporta issues de GitLab a markdown con comentarios y relaciones."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("GITLAB_BASE_URL", "https://gitlab.com"),
        help="URL base de GitLab o del API",
    )
    parser.add_argument("--project-id", default=os.getenv("GITLAB_PROJECT_ID"))
    parser.add_argument("--token", default=os.getenv("GITLAB_API_TOKEN"))
    parser.add_argument("--state", default=os.getenv("GITLAB_ISSUE_STATE", "all"))
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[2] / "rag" / "gitlab"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.project_id:
        raise SystemExit("Missing project id. Use --project-id or GITLAB_PROJECT_ID")
    if not args.token:
        raise SystemExit("Missing API token. Use --token or GITLAB_API_TOKEN")

    base_url = normalize_api_base_url(args.base_url)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching issues from GitLab...")
    issues = fetch_issues(base_url, args.project_id, args.token, args.state)
    print(f"Found {len(issues)} issues")
    project_prefix = sanitize_filename(args.project_id)

    for issue in issues:
        issue_iid = issue.get("iid")
        if issue_iid is None:
            continue

        notes = fetch_issue_notes(base_url, args.project_id, issue_iid, args.token)
        links = fetch_issue_links(base_url, args.project_id, issue_iid, args.token)
        markdown = format_issue_markdown(issue, notes, links)
        save_issue_markdown(issue, markdown, output_dir, project_prefix)

    print(f"Export complete in {output_dir}")


if __name__ == "__main__":
    main()
