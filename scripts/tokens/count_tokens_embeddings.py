#!/usr/bin/env python3
import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import tiktoken


@dataclass
class ModelCountResult:
    model: str
    total_tokens: int
    avg_tokens_per_file: float
    max_tokens: int
    max_tokens_file: str
    method: str


def count_tokens_tiktoken(encoding_name: str, text: str) -> int:
    encoder = tiktoken.get_encoding(encoding_name)
    return len(encoder.encode(text))


def estimate_tokens_google(text: str) -> int:
    # Practical approximation for Gemini usage when no local tokenizer is available.
    return max(1, math.ceil(len(text) / 4))


def select_counter(model: str) -> tuple[Callable[[str], int], str]:
    normalized = model.strip().lower()

    if normalized in {
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    }:
        return lambda text: count_tokens_tiktoken(
            "cl100k_base", text
        ), "exact(cl100k_base)"

    if normalized in {
        "gemini-embedding-001",
        "gemini-embedding-2",
        "gemini-embedding-2-preview",
    }:
        return estimate_tokens_google, "estimated(chars/4)"

    # Fallback for other OpenAI-compatible embeddings.
    return lambda text: count_tokens_tiktoken(
        "cl100k_base", text
    ), "estimated(cl100k_base fallback)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cuenta tokens en archivos markdown de rag/ para distintos modelos de embeddings."
        )
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[2] / "rag"),
        help="Directorio raíz de documentos a analizar.",
    )
    parser.add_argument(
        "--glob",
        default="**/*.md",
        help="Patrón glob a usar dentro de --root.",
    )
    parser.add_argument(
        "--models",
        default="text-embedding-3-small,text-embedding-3-large,gemini-embedding-001,gemini-embedding-2",
        help="Lista separada por comas de modelos de embeddings.",
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="Ruta opcional para exportar detalle por archivo/modelo en CSV.",
    )
    return parser.parse_args()


def read_markdown_files(root: Path, glob_pattern: str) -> list[Path]:
    return sorted(p for p in root.glob(glob_pattern) if p.is_file())


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    if not root.exists():
        raise SystemExit(f"El directorio no existe: {root}")
    if not models:
        raise SystemExit("No se especificaron modelos en --models")

    files = read_markdown_files(root, args.glob)
    if not files:
        raise SystemExit(
            f"No se encontraron archivos para {root} con glob '{args.glob}'"
        )

    file_texts: list[tuple[str, str]] = []
    for path in files:
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="replace")
        file_texts.append((rel, text))

    results: list[ModelCountResult] = []
    csv_rows: list[tuple[str, str, int, str]] = []

    for model in models:
        counter, method = select_counter(model)
        per_file: list[tuple[str, int]] = []

        for rel_path, text in file_texts:
            tokens = counter(text)
            per_file.append((rel_path, tokens))
            csv_rows.append((model, rel_path, tokens, method))

        total_tokens = sum(tokens for _, tokens in per_file)
        avg_tokens = total_tokens / len(per_file)
        max_file, max_tokens = max(per_file, key=lambda x: x[1])

        results.append(
            ModelCountResult(
                model=model,
                total_tokens=total_tokens,
                avg_tokens_per_file=avg_tokens,
                max_tokens=max_tokens,
                max_tokens_file=max_file,
                method=method,
            )
        )

    print(f"Root: {root}")
    print(f"Archivos analizados: {len(file_texts)}")
    print("")
    print(
        f"{'MODEL':36} {'TOTAL_TOKENS':>14} {'AVG/FILE':>12} {'MAX_FILE_TOKENS':>16}  METHOD"
    )
    print("-" * 100)
    for row in results:
        print(
            f"{row.model:36} {row.total_tokens:14d} {row.avg_tokens_per_file:12.2f} "
            f"{row.max_tokens:16d}  {row.method}"
        )
        print(f"  max_file: {row.max_tokens_file}")

    if args.csv:
        csv_path = Path(args.csv).resolve()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["model", "file", "tokens", "method"])
            writer.writerows(csv_rows)
        print("")
        print(f"CSV escrito en: {csv_path}")


if __name__ == "__main__":
    main()
