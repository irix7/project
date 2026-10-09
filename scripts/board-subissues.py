#!/usr/bin/env python3
"""Convert a product's draft item to a real issue and create command sub-issues.

The board's products are draft issues (no sub-task support). This converts one
product into a real issue in irix7/project, enumerates its source units from
the dist IDB, and creates one real issue per unit attached as a sub-issue (and
added to the project with src=full). The sub-issues are the claimable units
parallel agents work, and their Status/rebuild fields roll up into sub-issues
progress + percent-complete.

Usage:
  scripts/board-subissues.py --tree DIR --product eoe.sw.base [--limit N] [--dry-run]
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
REPO = "irix7/project"
PROJECT_ID = "PVT_kwDOFBsSF84BmAM2"
REPO_ID = "R_kgDOU6D_nA"


def gh(args, check=True):
    p = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if check and p.returncode != 0:
        sys.stderr.write(p.stderr)
        sys.exit(p.returncode or 1)
    return p


def gql(query, **vars_):
    args = ["api", "graphql", "-f", "query=" + query]
    for k, v in vars_.items():
        args += ["-F", f"{k}={v}"]
    out = gh(args)
    data = json.loads(out.stdout)
    if "errors" in data:
        sys.exit("gql error: " + json.dumps(data["errors"])[:800])
    return data["data"]


def source_dir(src):
    m = re.search(r"(?:^|/)(eoe|irix|stand|decompiled)/([^/]+)/([^/]+)", src)
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else None


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
                d = source_dir(toks[5])
                if d:
                    prod[toks[6]].add(d)
    return {p: sorted(ds) for p, ds in prod.items()}


def project_items():
    """Return {title: {"item_id", "type", "content_id", "content_type"}} for all items."""
    result = {}
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        q = (
            "query {"
            f'  node(id: "{PROJECT_ID}") {{'
            "    ... on ProjectV2 {"
            f"      items(first: 100{after}) {{"
            "        pageInfo { hasNextPage endCursor }"
            "        nodes { id type content { ... on DraftIssue { id title } ... on Issue { id title } } }"
            "      }"
            "    }"
            "  }"
            "}"
        )
        data = gql(q)
        items = data["node"]["items"]
        for n in items["nodes"]:
            c = n.get("content") or {}
            if c.get("title"):
                result[c["title"]] = {
                    "item_id": n["id"],
                    "type": n.get("type"),
                    "content_id": c.get("id"),
                }
        if not items["pageInfo"]["hasNextPage"]:
            break
        cursor = items["pageInfo"]["endCursor"]
    return result


def convert(item_id):
    d = gql(
        "mutation($i:ID!,$r:ID!){ convertProjectV2DraftIssueItemToIssue(input:{itemId:$i,repositoryId:$r}){ item { id content { ... on Issue { id number title } } } } }",
        i=item_id, r=REPO_ID,
    )
    return d["convertProjectV2DraftIssueItemToIssue"]["item"]["content"]


def existing_sub_issue_titles(parent_issue_id):
    titles = set()
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        d = gql(
            "query($id:ID!,$a:String){ node(id:$id){ ... on Issue { subIssues(first:100, after:$a){ pageInfo { hasNextPage endCursor } nodes { title } } } } }",
            id=parent_issue_id, a=cursor or "",
        )
        si = d["node"]["subIssues"]
        for n in si["nodes"]:
            titles.add(n["title"])
        if not si["pageInfo"]["hasNextPage"]:
            break
        cursor = si["pageInfo"]["endCursor"]
    return titles


def create_issue(title, body=""):
    d = gql(
        "mutation($r:ID!,$t:String!,$b:String!){ createIssue(input:{repositoryId:$r,title:$t,body:$b}){ issue { id number } } }",
        r=REPO_ID, t=title, b=body,
    )
    return d["createIssue"]["issue"]


def add_sub_issue(parent_id, child_id):
    gql(
        "mutation($p:ID!,$c:ID!){ addSubIssue(input:{issueId:$p,subIssueId:$c}){ issue { id } } }",
        p=parent_id, c=child_id,
    )


def add_to_project(content_id):
    d = gql(
        "mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c}){ item { id } } }",
        p=PROJECT_ID, c=content_id,
    )
    return d["addProjectV2ItemById"]["item"]["id"]


def field_map():
    out = gh(["project", "field-list", "1", "--owner", OWNER, "--format", "json"])
    fields = {}
    for f in json.loads(out.stdout)["fields"]:
        if f.get("type") == "ProjectV2SingleSelectField":
            fields[f["name"]] = {
                "id": f["id"],
                "options": {o["name"]: o["id"] for o in f.get("options", [])},
            }
    return fields


def set_field(item_id, fields, name, value):
    gql(
        "mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){ projectV2Item { id } } }",
        p=PROJECT_ID, i=item_id, f=fields[name]["id"], o=fields[name]["options"][value],
    )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="board-subissues.py")
    ap.add_argument("--tree", required=True)
    ap.add_argument("--product", required=True)
    ap.add_argument("--limit", type=int, default=0, help="max sub-issues (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    idb_dir = os.path.join(args.tree, ".recovery", "idb")
    if not os.path.isdir(idb_dir):
        sys.exit(f"no idb dir: {idb_dir}")
    prod = parse_idb(idb_dir)
    if args.product not in prod:
        sys.exit(f"product {args.product!r} not in idb (have {len(prod)} products)")

    units = prod[args.product]
    if args.limit:
        units = units[: args.limit]

    items = project_items()
    if args.product not in items:
        sys.exit(f"no board item titled {args.product!r}")

    fields = field_map()
    rec = items[args.product]

    # Resolve the product issue: convert if still a draft, else reuse.
    if rec["type"] == "DRAFT_ISSUE":
        if args.dry_run:
            parent = {"id": rec["content_id"], "number": "?"}
            print(f"[dry-run] convert {args.product} -> issue")
        else:
            parent = convert(rec["item_id"])
            print(f"converted {args.product} -> issue #{parent['number']} ({parent['id']})")
    else:
        parent = {"id": rec["content_id"], "number": "?"}
        print(f"{args.product} is already issue {rec['content_id']}")

    if args.dry_run:
        print(f"[dry-run] create {len(units)} sub-issues, attach, add to project, src=full")
        for u in units[:10]:
            print("   -", u)
        return

    existing = existing_sub_issue_titles(parent["id"])
    to_create = [u for u in units if u not in existing]
    print(f"{args.product}: {len(to_create)} new sub-issues ({len(existing)} already present)")

    done = 0
    for u in to_create:
        issue = create_issue(u)
        add_sub_issue(parent["id"], issue["id"])
        new_item = add_to_project(issue["id"])
        set_field(new_item, fields, "src", "full")
        done += 1
        print(f"  [{done}/{len(to_create)}] {u} -> issue #{issue['number']}")

    print(f"done: {args.product} -> {len(to_create)} new sub-issues, {len(existing)} total existing")


if __name__ == "__main__":
    main()
