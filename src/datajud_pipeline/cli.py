"""Linha de comando: `datajud ingest` e `datajud transform`."""

from __future__ import annotations

import argparse
import logging

from .ingestion import ingest
from .transform import transform


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="datajud")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ing = sub.add_parser("ingest", help="baixa processos da API DataJud para a camada raw")
    p_ing.add_argument("--tribunal", required=True, help="sigla do tribunal, ex.: tjsp, trt2")
    p_ing.add_argument("--classe", type=int, help="código da classe processual (TPU)")
    p_ing.add_argument("--ajuizado-desde", help="data mínima de ajuizamento, ex.: 2020-01-01")
    p_ing.add_argument("--max-docs", type=int, default=5000, help="limite de documentos")
    p_ing.add_argument(
        "--resume",
        action="store_true",
        help="continua a última ingestão incompleta do tribunal, com os filtros dela",
    )

    sub.add_parser("transform", help="gera as camadas silver e gold no DuckDB")

    args = parser.parse_args(argv)
    if args.cmd == "ingest":
        ingest(
            args.tribunal, args.classe, args.ajuizado_desde, args.max_docs, resume=args.resume
        )
    elif args.cmd == "transform":
        transform()


if __name__ == "__main__":
    main()
