"""Registro de los .md generados por los scripts de extraccion (Kiwi, GitLab).

Base SQLite en scripts/registry/registry.db con dos tablas:

- items: un registro por item (caso de prueba o issue) y fuente (kiwi,
  gitlab_<proyecto>) con path actual del .md, fecha de descarga (creacion
  del .md), fecha de ultima actualizacion, version actual y estado en origen.
- versions: una fila por cada version del .md (se crea una nueva cada vez que
  el contenido cambia) con hash, fecha, fecha de actualizacion en origen y el
  contenido comprimido con gzip.

La base vive fuera de rag/ para que el indexador (rag/**/*.md) no la vea.

Uso como CLI:
    python md_registry.py status [--source kiwi] [--csv reporte.csv] [--all]
    python md_registry.py history <fuente> <id> [--show N]
"""

import argparse
import csv
import gzip
import hashlib
import signal
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
RAG_DIR = REPO_ROOT / "rag"
DB_PATH = SCRIPTS_DIR / "registry" / "registry.db"

# Estados persistidos en el registro (dependen del origen)
REMOTE_OK = "ok"
REMOTE_REMOVED = "eliminado_remoto"
REMOTE_NOT_DOWNLOADED = "sin_descargar"

# Estados calculados al generar el reporte (dependen del disco)
STATUS_OK = "descargado"
STATUS_LOCAL_EDIT = "modificado_local"
STATUS_MISSING = "faltante"
STATUS_UNREGISTERED = "no_registrado"

# Cada cuantas escrituras se confirma la transaccion
COMMIT_EVERY = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    source           TEXT NOT NULL,
    item_id          TEXT NOT NULL,
    title            TEXT,
    path             TEXT,
    url              TEXT,
    first_downloaded TEXT,
    last_updated     TEXT,
    last_synced      TEXT,
    source_created   TEXT,
    source_updated   TEXT,
    version          INTEGER NOT NULL DEFAULT 0,
    sha256           TEXT,
    remote_status    TEXT NOT NULL DEFAULT 'ok',
    note             TEXT,
    PRIMARY KEY (source, item_id)
);

CREATE TABLE IF NOT EXISTS versions (
    source         TEXT NOT NULL,
    item_id        TEXT NOT NULL,
    version        INTEGER NOT NULL,
    date           TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    source_updated TEXT,
    path           TEXT,
    content_gz     BLOB,
    PRIMARY KEY (source, item_id, version),
    FOREIGN KEY (source, item_id) REFERENCES items (source, item_id)
);

CREATE INDEX IF NOT EXISTS idx_items_path ON items (path);
"""


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def mtime_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, UTC).astimezone().isoformat(timespec="seconds")


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def rel_path(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def abs_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


class Registry:
    def __init__(self, source: str):
        self.source = source
        self.conn = connect()
        self.pending = 0
        self.stats = {"nuevos": 0, "actualizados": 0, "sin_cambios": 0, "renombrados": 0}

    def get(self, item_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM items WHERE source = ? AND item_id = ?",
            (self.source, item_id),
        ).fetchone()

    def write(
        self,
        item_id: str | int,
        filepath: Path,
        content: str,
        title: str = "",
        source_created: str | None = None,
        source_updated: str | None = None,
        url: str | None = None,
    ) -> str:
        """Escribe el .md si cambio y registra la version. Devuelve la accion."""
        key = str(item_id)
        now = now_iso()
        digest = sha256(content)
        new_rel = rel_path(filepath)
        entry = self.get(key)

        if entry is None or entry["version"] == 0:
            action = self._first_write(
                key, filepath, content, digest, new_rel, now, title,
                source_created, source_updated, url,
            )
            self._tick()
            return action

        self.conn.execute(
            """UPDATE items SET last_synced = ?, remote_status = ?,
                   title = COALESCE(NULLIF(?, ''), title),
                   url = COALESCE(?, url),
                   source_created = COALESCE(?, source_created),
                   source_updated = COALESCE(?, source_updated)
               WHERE source = ? AND item_id = ?""",
            (now, REMOTE_OK, title, url, source_created, source_updated, self.source, key),
        )

        old_path = abs_path(entry["path"]) if entry["path"] else None
        renamed = old_path is not None and old_path.resolve() != filepath.resolve()
        if renamed and old_path.exists():
            # Cambio el titulo: se borra el .md viejo para no duplicarlo en el RAG
            old_path.unlink()
            self.stats["renombrados"] += 1

        if entry["sha256"] == digest:
            # Mismo contenido: solo se restaura el .md si falta (renombrado o borrado)
            if not filepath.exists():
                self._write_file(filepath, content)
            self._set_path(key, new_rel)
            self.stats["sin_cambios"] += 1
            self._tick()
            return "renombrado" if renamed else "sin_cambios"

        version = entry["version"] + 1
        self._write_file(filepath, content)
        self._add_version(key, version, now, digest, source_updated, new_rel, content)
        self.conn.execute(
            """UPDATE items SET version = ?, sha256 = ?, last_updated = ?, path = ?
               WHERE source = ? AND item_id = ?""",
            (version, digest, now, new_rel, self.source, key),
        )
        self.stats["actualizados"] += 1
        self._tick()
        return "actualizado"

    def _first_write(
        self, key, filepath, content, digest, new_rel, now, title,
        source_created, source_updated, url,
    ) -> str:
        existing = filepath.read_text(encoding="utf-8") if filepath.exists() else None
        # .md descargado antes de existir el registro: se toma su mtime
        first = mtime_iso(filepath) if existing is not None else now
        note = "fecha de descarga inferida del mtime del archivo" if existing is not None else None

        self.conn.execute(
            """INSERT INTO items (source, item_id, title, path, url, first_downloaded,
                   last_updated, last_synced, source_created, source_updated,
                   version, sha256, remote_status, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT (source, item_id) DO UPDATE SET
                   title = excluded.title, path = excluded.path, url = excluded.url,
                   first_downloaded = excluded.first_downloaded,
                   last_updated = excluded.last_updated,
                   last_synced = excluded.last_synced,
                   source_created = excluded.source_created,
                   source_updated = excluded.source_updated,
                   version = 1, sha256 = excluded.sha256,
                   remote_status = excluded.remote_status, note = excluded.note""",
            (
                self.source, key, title, new_rel, url, first, first, now,
                source_created, source_updated,
                sha256(existing) if existing is not None else digest,
                REMOTE_OK, note,
            ),
        )

        if existing is None:
            self._write_file(filepath, content)
            self._add_version(key, 1, now, digest, source_updated, new_rel, content)
            self.stats["nuevos"] += 1
            return "nuevo"

        if sha256(existing) == digest:
            self._add_version(key, 1, first, digest, source_updated, new_rel, content)
            self.stats["sin_cambios"] += 1
            return "sin_cambios"

        # Habia un .md previo distinto: queda como v1 y el nuevo como v2
        self._add_version(key, 1, first, sha256(existing), None, new_rel, existing)
        self._write_file(filepath, content)
        self._add_version(key, 2, now, digest, source_updated, new_rel, content)
        self.conn.execute(
            """UPDATE items SET version = 2, sha256 = ?, last_updated = ?
               WHERE source = ? AND item_id = ?""",
            (digest, now, self.source, key),
        )
        self.stats["actualizados"] += 1
        return "actualizado"

    def mark_remote(self, seen_ids, not_downloaded: dict | None = None) -> None:
        """Marca el estado en origen tras un listado completo.

        seen_ids: ids devueltos por el origen y descargados.
        not_downloaded: ids que existen en origen pero no se descargaron
            (ej. GitLab con state=opened), con metadatos basicos.
        """
        seen = {str(i) for i in seen_ids}
        not_downloaded = {str(k): v for k, v in (not_downloaded or {}).items()}
        now = now_iso()

        rows = self.conn.execute(
            "SELECT item_id, version FROM items WHERE source = ?", (self.source,)
        ).fetchall()
        known = set()
        for row in rows:
            key = row["item_id"]
            known.add(key)
            if key in seen:
                continue
            if key in not_downloaded:
                status = REMOTE_NOT_DOWNLOADED if row["version"] == 0 else REMOTE_OK
                self.conn.execute(
                    """UPDATE items SET remote_status = ?, last_synced = ?
                       WHERE source = ? AND item_id = ?""",
                    (status, now, self.source, key),
                )
            else:
                self.conn.execute(
                    "UPDATE items SET remote_status = ? WHERE source = ? AND item_id = ?",
                    (REMOTE_REMOVED, self.source, key),
                )

        for key, meta in not_downloaded.items():
            if key in known:
                continue
            self.conn.execute(
                """INSERT INTO items (source, item_id, title, url, last_synced,
                       source_created, source_updated, version, remote_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    self.source, key, meta.get("title"), meta.get("url"), now,
                    meta.get("source_created"), meta.get("source_updated"),
                    REMOTE_NOT_DOWNLOADED,
                ),
            )

    def save(self) -> None:
        self.conn.commit()
        self.pending = 0

    def close(self) -> None:
        self.save()
        self.conn.close()

    def summary(self) -> str:
        s = self.stats
        return (
            f"Registro {self.source}: {s['nuevos']} nuevos, {s['actualizados']} actualizados, "
            f"{s['sin_cambios']} sin cambios, {s['renombrados']} renombrados"
        )

    def _tick(self) -> None:
        self.pending += 1
        if self.pending >= COMMIT_EVERY:
            self.save()

    def _set_path(self, key: str, path: str) -> None:
        self.conn.execute(
            "UPDATE items SET path = ? WHERE source = ? AND item_id = ?",
            (path, self.source, key),
        )

    def _add_version(self, key, version, date, digest, source_updated, path, content) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO versions
                   (source, item_id, version, date, sha256, source_updated, path, content_gz)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.source, key, version, date, digest, source_updated, path,
                gzip.compress(content.encode("utf-8")),
            ),
        )

    @staticmethod
    def _write_file(filepath: Path, content: str) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")


def local_status(entry: sqlite3.Row) -> str:
    if not entry["path"]:
        return entry["remote_status"] or REMOTE_NOT_DOWNLOADED
    path = abs_path(entry["path"])
    if not path.exists():
        return STATUS_MISSING
    if sha256(path.read_text(encoding="utf-8")) != entry["sha256"]:
        return STATUS_LOCAL_EDIT
    if entry["remote_status"] == REMOTE_REMOVED:
        return REMOTE_REMOVED
    return STATUS_OK


def build_rows(conn: sqlite3.Connection, source: str | None) -> list[dict]:
    query = "SELECT * FROM items"
    params: tuple = ()
    if source:
        query += " WHERE source = ? OR source LIKE ?"
        params = (source, f"{source}\\_%")
        query += " ESCAPE '\\'"

    rows = []
    registered_paths = set()
    for entry in conn.execute(query, params):
        if entry["path"]:
            registered_paths.add(str(abs_path(entry["path"]).resolve()))
        rows.append(
            {
                "fuente": entry["source"],
                "id": entry["item_id"],
                "estado": local_status(entry),
                "version": entry["version"],
                "descargado": entry["first_downloaded"] or "",
                "actualizado": entry["last_updated"] or "",
                "ultima_sync": entry["last_synced"] or "",
                "actualizado_origen": entry["source_updated"] or "",
                "titulo": entry["title"] or "",
                "path": entry["path"] or "",
            }
        )

    # .md presentes en rag/ que ningun registro conoce
    if RAG_DIR.exists() and not source:
        for path in sorted(RAG_DIR.rglob("*.md")):
            if str(path.resolve()) in registered_paths:
                continue
            rows.append(
                {
                    "fuente": "",
                    "id": "",
                    "estado": STATUS_UNREGISTERED,
                    "version": "",
                    "descargado": mtime_iso(path),
                    "actualizado": mtime_iso(path),
                    "ultima_sync": "",
                    "actualizado_origen": "",
                    "titulo": "",
                    "path": rel_path(path),
                }
            )
    return rows


def sort_id(value: str):
    return (0, int(value)) if value.isdigit() else (1, value)


def cmd_status(args: argparse.Namespace) -> None:
    with connect() as conn:
        rows = build_rows(conn, args.source)
    rows.sort(key=lambda r: (r["fuente"], sort_id(r["id"]), r["path"]))
    if not rows:
        print("Registro vacio. Ejecutar ./sync.sh kiwi o ./sync.sh gitlab primero.")
        return

    print(f"{'FUENTE':30} {'ESTADO':18} {'TOTAL':>6} {'CON VERSIONES':>14}")
    print("-" * 72)
    totals: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        t = totals.setdefault((r["fuente"] or "-", r["estado"]), [0, 0])
        t[0] += 1
        if isinstance(r["version"], int) and r["version"] > 1:
            t[1] += 1
    for (fuente, estado), (count, versioned) in sorted(totals.items()):
        print(f"{fuente:30} {estado:18} {count:6d} {versioned:14d}")

    shown = rows if args.all else [
        r for r in rows
        if r["estado"] != STATUS_OK or (isinstance(r["version"], int) and r["version"] > 1)
    ]
    if shown:
        label = "Detalle" if args.all else "Detalle (no descargados, con cambios o versionados)"
        print(f"\n{label}:")
        print(f"{'FUENTE':22} {'ID':>7} {'ESTADO':17} {'V':>3} {'DESCARGADO':25} {'ACTUALIZADO':25} PATH")
        for r in shown:
            print(
                f"{r['fuente'][:22]:22} {r['id']:>7} {r['estado']:17} {r['version']!s:>3} "
                f"{r['descargado']:25} {r['actualizado']:25} {r['path']}"
            )

    if args.csv:
        csv_path = Path(args.csv).resolve()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nCSV escrito en: {csv_path}")


def cmd_history(args: argparse.Namespace) -> None:
    with connect() as conn:
        entry = conn.execute(
            "SELECT * FROM items WHERE source = ? AND item_id = ?",
            (args.source, str(args.id)),
        ).fetchone()
        if entry is None:
            raise SystemExit(f"No existe {args.id} en el registro {args.source}")
        versions = conn.execute(
            """SELECT * FROM versions WHERE source = ? AND item_id = ?
               ORDER BY version""",
            (args.source, str(args.id)),
        ).fetchall()

    if args.show is not None:
        match = [v for v in versions if v["version"] == args.show]
        if not match:
            raise SystemExit(f"No existe la version {args.show}")
        print(gzip.decompress(match[0]["content_gz"]).decode("utf-8"), end="")
        return

    print(f"{entry['title']}  [{local_status(entry)}]")
    print(f"path: {entry['path']}")
    print(f"url:  {entry['url']}")
    print(f"descargado: {entry['first_downloaded']}  actualizado: {entry['last_updated']}")
    if entry["note"]:
        print(f"nota: {entry['note']}")
    print(f"\n{'V':>3} {'FECHA':25} {'ACTUALIZADO ORIGEN':28} {'SHA256':12} PATH")
    for v in versions:
        print(
            f"{v['version']:>3} {v['date']:25} {v['source_updated'] or '':28} "
            f"{v['sha256'][:12]:12} {v['path']}"
        )


def main() -> None:
    # Evita BrokenPipeError al cortar la salida con head/less
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    parser = argparse.ArgumentParser(description="Registro de .md descargados")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Resumen de items mapeados y no mapeados")
    p_status.add_argument("--source", help="Filtrar por fuente (kiwi, gitlab, gitlab_<proyecto>)")
    p_status.add_argument("--csv", help="Exportar detalle completo a CSV")
    p_status.add_argument("--all", action="store_true", help="Listar todos los items")
    p_status.set_defaults(func=cmd_status)

    p_hist = sub.add_parser("history", help="Historial de versiones de un item")
    p_hist.add_argument("source", help="Fuente (kiwi o gitlab_<proyecto>)")
    p_hist.add_argument("id", help="ID del caso de prueba o IID del issue")
    p_hist.add_argument("--show", type=int, help="Imprime el contenido de esa version")
    p_hist.set_defaults(func=cmd_history)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
