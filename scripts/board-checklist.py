#!/usr/bin/env python3
"""Write a per-command rebuild checklist into a product's board item body.

Parses the IRIX dist IDB files (private tree's .recovery/idb) to enumerate
each product's source directories, then writes that list as a markdown
task-list into the product's board item body. Agents tick units off without
re-graining the board (granularity decision: products stay items; sub-progress
lives in the body).

Usage:
  scripts/board-checklist.py --tree DIR list <product>          # print the dirs
  scripts/board-checklist.py --tree DIR write <product> [--dry-run]
  scripts/board-checklist.py --tree DIR write --all [--dry-run]
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

OWNER = "irix7"
PROJECT_NUMBER = "1"
PROJECT_ID = "PVT_kwDOFBsSF84BmAM2"


def gh(args, check=True):
    p = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if check and p.returncode != 0:
        sys.stderr.write(p.stderr)
        sys.exit(p.returncode or 1)
    return p


def source_dir(src):
    """Map a dist source path to its buildable tree directory (root/sub/name)."""
    m = re.search(r"(?:^|/)(eoe|irix|stand|decompiled)/([^/]+)/([^/]+)", src)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return None


def parse_idb(idb_dir):
    prod = defaultdict(set)
    for path in glob.glob(os.path.join(idb_dir, "*.idb")):
        with open(path, encoding="latin-1") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line or line[0] not in "fl":
                    continue
                toks = line.split()
                if len(toks) < 7:
                    continue
                src, product = toks[5], toks[6]
                d = source_dir(src)
                if d:
                    prod[product].add(d)
    return {p: sorted(d) for p, d in prod.items()}


def board_items():
    """Return {title: item_id} for every draft-issue item in the project."""
    nodes = []
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        q = (
            "query {"
            f'  node(id: "{PROJECT_ID}") {{'
            "    ... on ProjectV2 {"
            f"      items(first: 100{after}) {{"
            "        pageInfo { hasNextPage endCursor }"
            "        nodes { id content { ... on DraftIssue { title } } }"
            "      }"
            "    }"
            "  }"
            "}"
        )
        out = gh(["api", "graphql", "-f", "query=" + q])
        data = json.loads(out.stdout)
        items = data["data"]["node"]["items"]
        for n in items["nodes"]:
            title = (n.get("content") or {}).get("title")
            if title:
                nodes.append((title, n["id"]))
        if not items["pageInfo"]["hasNextPage"]:
            break
        cursor = items["pageInfo"]["endCursor"]
    return dict(nodes)


def checklist_body(product, dirs):
    head = [
        f"# {product} — rebuild checklist",
        "",
        "Sub-progress lives here; the board fields track the aggregate state.",
        f"{len(dirs)} source units:",
        "",
    ]
    body = "\n".join(head + [f"- [ ] `{d}`" for d in dirs])
    return body + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="board-checklist.py")
    ap.add_argument("--tree", required=True, help="IRIX source checkout")
    ap.add_argument("command", choices=["list", "write"])
    ap.add_argument("product", help="product name, or --all")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    idb_dir = os.path.join(args.tree, ".recovery", "idb")
    if not os.path.isdir(idb_dir):
        sys.exit(f"no idb dir: {idb_dir}")
    prod = parse_idb(idb_dir)

    if args.product == "--all":
        targets = list(prod.items())
    else:
        if args.product not in prod:
            sys.exit(f"product {args.product!r} not in the idb (have {len(prod)} products)")
        targets = [(args.product, prod[args.product])]

    items = board_items()

    for product, dirs in targets:
        body = checklist_body(product, dirs)
        if args.command == "list":
            print(f"{product}: {len(dirs)} dirs")
            for d in dirs:
                print("  ", d)
            continue
        # write
        if product not in items:
            print(f"skip {product}: no board item (idb-only product)")
            continue
        item_id = items[product]
        cmd = ["project", "item-edit", "--id", item_id,
               "--project-id", PROJECT_ID, "--body", body]
        if args.dry_run:
            print(f"[dry-run] gh " + " ".join(cmd[:6]) + f" --body '<{len(dirs)} dirs>'")
            continue
        gh(cmd)
        print(f"wrote {len(dirs)} dirs to {product}")


if __name__ == "__main__":
    main()
