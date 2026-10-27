import os
import re
import ssl
import sys
from configparser import ConfigParser
from pathlib import Path
from tcms_api import TCMS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from md_registry import Registry


def create_ssl_context():
    try:
        _create_unverified_https_context = ssl._create_unverified_context
    except AttributeError:
        return
    ssl._create_default_https_context = _create_unverified_https_context


def load_tcms_config():
    env_url = os.getenv("TCMS_URL")
    if env_url:
        return env_url, os.getenv("TCMS_USERNAME"), os.getenv("TCMS_PASSWORD")

    env_conf = Path(__file__).resolve().parents[1] / "env" / ".tcms.conf"
    if env_conf.exists():
        config = ConfigParser()
        config.read(env_conf)
        return (
            config["tcms"].get("url"),
            config["tcms"].get("username"),
            config["tcms"].get("password"),
        )
    return None, None, None


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


def to_markdown(test_case: dict) -> str:
    summary = test_case.get("summary") or test_case.get("name") or "No summary"
    lines = [f"# {summary}", ""]

    fields = [
        ("ID", test_case.get("id") or test_case.get("pk")),
        ("Status", test_case.get("case_status")),
        ("Priority", test_case.get("priority")),
        ("Category", test_case.get("category")),
        ("Author", test_case.get("author")),
        ("Is automated", test_case.get("is_automated")),
        ("Created", test_case.get("created")),
        ("Updated", test_case.get("updated")),
    ]

    for label, value in fields:
        if value is not None:
            lines.append(f"- **{label}**: {value}")
    lines.append("")

    text = test_case.get("text") or ""
    notes = test_case.get("notes") or ""
    if text:
        lines.extend(["## Description", "", text.strip(), ""])
    if notes:
        lines.extend(["## Notes", "", notes.strip(), ""])

    # Add any remaining custom fields not already in the header
    known_keys = {
        "id",
        "pk",
        "summary",
        "name",
        "case_status",
        "priority",
        "category",
        "author",
        "is_automated",
        "created",
        "updated",
        "text",
        "notes",
    }
    extra = {
        k: v for k, v in test_case.items() if k not in known_keys and v is not None
    }
    if extra:
        lines.extend(["## Additional fields", ""])
        for key, value in sorted(extra.items()):
            lines.append(f"- **{key}**: {value}")

    return "\n".join(lines).strip() + "\n"


def optional_str(value) -> str | None:
    return None if value is None else str(value)


def write_markdown_files(test_cases: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    registry = Registry("kiwi")
    seen = []
    for test_case in test_cases:
        case_id = test_case.get("id") or test_case.get("pk")
        if case_id is None:
            continue

        summary = test_case.get("summary") or test_case.get("name") or "unnamed"
        filename = f"tc_{case_id}_{sanitize_filename(summary)}.md"
        filepath = output_dir / filename
        action = registry.write(
            case_id,
            filepath,
            to_markdown(test_case),
            title=summary,
            source_created=optional_str(test_case.get("created")),
            source_updated=optional_str(test_case.get("updated")),
        )
        seen.append(case_id)
        print(f"[{action}] {filepath}")

    # TestCase.filter({}) trae todos los casos: lo que no vino fue eliminado
    registry.mark_remote(seen)
    registry.save()
    print(registry.summary())


def main():
    create_ssl_context()
    url, username, password = load_tcms_config()
    rpc = TCMS(url, username, password).exec

    print("Fetching test cases from Kiwi TCMS...")
    test_cases = rpc.TestCase.filter({})
    output_dir = Path(__file__).resolve().parents[2] / "rag" / "kiwi"
    write_markdown_files(test_cases, output_dir)
    print(f"Export complete: {len(test_cases)} files created in {output_dir}")


if __name__ == "__main__":
    main()
