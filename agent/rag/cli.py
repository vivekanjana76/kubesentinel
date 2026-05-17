"""
CLI for manual retrieval sanity-checking.

Usage:
    py -3.12 -m agent.rag.cli query "OOMKilled with memory limit 128Mi"
    py -3.12 -m agent.rag.cli query "pod restart loop image pull error" --k 5
"""

import argparse
import sys
import textwrap

from agent.rag.retriever import get_retriever


def cmd_query(query: str, k: int) -> None:
    retriever = get_retriever()
    results = retriever.retrieve(query, k=k)

    if not results:
        print("No results returned.")
        return

    print(f"\nQuery : {query!r}")
    print(f"Top-{k} results from runbooks store:\n")
    print("-" * 72)

    for i, r in enumerate(results, start=1):
        snippet = textwrap.shorten(r.content, width=200, placeholder="...")
        print(f"[{i}] {r.title}")
        print(f"    Source  : {r.source_file}")
        print(f"    Score   : {r.similarity:.4f}")
        print(f"    Preview : {snippet}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agent.rag.cli",
        description="Query the KubeSentinel runbook retriever.",
    )
    sub = parser.add_subparsers(dest="command")

    q_parser = sub.add_parser("query", help="Retrieve relevant runbooks for a query.")
    q_parser.add_argument("text", help="Natural-language query string.")
    q_parser.add_argument("--k", type=int, default=3, help="Number of results (1-20, default 3).")

    args = parser.parse_args()

    if args.command == "query":
        if not 1 <= args.k <= 20:
            print(f"Error: --k must be between 1 and 20, got {args.k}", file=sys.stderr)
            sys.exit(1)
        cmd_query(args.text, args.k)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
