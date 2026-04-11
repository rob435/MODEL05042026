from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
from pathlib import Path


def sqlite_row_count(db_path: Path) -> int:
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute("select count(*) from historical_candles")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        connection.close()


def pack_cache(source: Path, archive: Path, overwrite: bool = False) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"Source cache does not exist: {source}")
    if archive.exists() and not overwrite:
        raise FileExistsError(f"Archive already exists: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, gzip.open(archive, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    return archive


def unpack_cache(archive: Path, destination: Path, overwrite: bool = False) -> Path:
    if not archive.exists():
        raise FileNotFoundError(f"Archive does not exist: {archive}")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive, "rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    sqlite_row_count(destination)
    return destination


def inspect_cache(path: Path) -> str:
    row_count = sqlite_row_count(path)
    size_mb = path.stat().st_size / (1024 * 1024)
    return (
        f"Cache info\n"
        f"  path={path}\n"
        f"  size_mb={size_mb:.2f}\n"
        f"  historical_candles={row_count}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pack, unpack, or inspect the backtest cache.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pack_parser = subparsers.add_parser("pack", help="Compress a cache SQLite file into a .gz archive.")
    pack_parser.add_argument("--source", required=True, help="Source cache SQLite path.")
    pack_parser.add_argument("--archive", required=True, help="Destination .gz archive path.")
    pack_parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing archive.")

    unpack_parser = subparsers.add_parser("unpack", help="Restore a cache archive into a SQLite file.")
    unpack_parser.add_argument("--archive", required=True, help="Source .gz archive path.")
    unpack_parser.add_argument("--destination", required=True, help="Destination cache SQLite path.")
    unpack_parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite an existing destination cache."
    )

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a cache SQLite file.")
    inspect_parser.add_argument("--path", required=True, help="Cache SQLite path.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "pack":
        archive_path = pack_cache(Path(args.source), Path(args.archive), overwrite=args.overwrite)
        print(f"Packed cache archive to {archive_path}")
        return 0
    if args.command == "unpack":
        destination = unpack_cache(
            Path(args.archive),
            Path(args.destination),
            overwrite=args.overwrite,
        )
        print(inspect_cache(destination))
        return 0
    if args.command == "inspect":
        print(inspect_cache(Path(args.path)))
        return 0
    parser.error("Unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
