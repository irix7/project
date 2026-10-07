#!/usr/bin/env python3
"""Flatten a product into per-command board items grouped by subsystem.

GitHub caps single-select fields at 100 options and sub-issues at 100 per
parent, so the board uses a coarse `Subsystem` single-select (grouped view) and
a `Product` text field (exact .sw.* name). Each command is its own draft item
with src=full, and is individually claimable by a parallel agent.

Usage:
  scripts/board-flatten.py --tree DIR --product eoe.sw.base [--limit N] [--dry-run]
  scripts/board-flatten.py --tree DIR --product eoe.sw.base --cleanup-pilot
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

OWNER = "irix7"
REPO = "irix7/project"
PROJECT_ID = "PVT_kwDOFBsSF84BmAM2"
SUBSYSTEM_FIELD = "Subsystem"
PRODUCT_FIELD = "Product"


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


def subsystem(product):
    """Product family: strip `.sw...`, then `_dev`/`_eoe`/`_base` suffix."""
    head = re.sub(r"\.sw(\..*)?$", "", product)
    head = re.sub(r"_(dev|eoe|base)$", "", head)
    head = head.lstrip("_")
    return head or "misc"


def subsystem_buckets(products):
    fam = Counter(subsystem(p) for p in products)
    buckets = set()
    for p in products:
        s = subsystem(p)
        buckets.add(s if fam[s] >= 2 else "misc")
    return sorted(buckets)


def field_map():
    out = gh(["project", "field-list", "1", "--owner", OWNER, "--format", "json"])
    result = {}
    for f in json.loads(out.stdout)["fields"]:
        result[f["name"]] = {
            "id": f["id"],
            "type": f.get("type"),
            "options": {o["name"]: o["id"] for o in f.get("options", [])},
        }
    return result


def _opts(options):
    return ", ".join('{name:%s, color:GRAY, description:""}' % json.dumps(o) for o in options)


def create_field(name, datatype, options=None):
    extra = ""
    if options is not None:
        extra = ", singleSelectOptions:[%s]" % _opts(options)
    d = gql(
        "mutation($p:ID!,$n:String!,$t:ProjectV2CustomFieldType!){ createProjectV2Field(input:{projectId:$p, dataType:$t, name:$n%s}){ projectV2Field { ... on ProjectV2SingleSelectField { id } ... on ProjectV2Field { id } } } }" % extra,
        p=PROJECT_ID, n=name, t=datatype,
    )
    item = d["createProjectV2Field"]["projectV2Field"]
    if isinstance(item, list):
        item = item[0]
    return item["id"]


def delete_field(name):
    fields = field_map()
    if name in fields:
        gql(
            "mutation($f:ID!){ deleteProjectV2Field(input:{fieldId:$f}){ projectV2Field { ... on ProjectV2Field { id } ... on ProjectV2SingleSelectField { id } } } }",
            f=fields[name]["id"],
        )


def ensure_fields(products):
    """Ensure Subsystem (single-select) and Product (text) fields exist."""
    fields = field_map()
    buckets = subsystem_buckets(products)
    if SUBSYSTEM_FIELD in fields:
        sub_id = fields[SUBSYSTEM_FIELD]["id"]
        have = set(fields[SUBSYSTEM_FIELD]["options"])
        missing = [b for b in buckets if b not in have]
        if missing:
            add = ", ".join('{name:%s, color:GRAY, description:""}' % json.dumps(b) for b in missing)
            gql("mutation($f:ID!){ updateProjectV2Field(input:{fieldId:$f, singleSelectOptions:[%s]}){ projectV2Field { ... on ProjectV2SingleSelectField { id } } } }" % add, f=sub_id)
    else:
        create_field(SUBSYSTEM_FIELD, "SINGLE_SELECT", buckets[:100])
        if len(buckets) > 100:
            sys.exit(f"too many subsystem buckets: {len(buckets)}")
    if PRODUCT_FIELD not in fields:
        create_field(PRODUCT_FIELD, "TEXT")
    return field_map()


def project_item_titles():
    titles = {}
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        q = (
            "query {"
            f'  node(id: "{PROJECT_ID}") {{'
            "    ... on ProjectV2 {"
            f"      items(first: 100{after}) {{"
            "        pageInfo { hasNextPage endCursor }"
            "        nodes { id content { ... on DraftIssue { title } ... on Issue { title } } }"
            "      }"
            "    }"
            "  }"
            "}"
        )
        d = gql(q)
        items = d["node"]["items"]
        for n in items["nodes"]:
            c = n.get("content") or {}
            if c.get("title"):
                titles[c["title"]] = n["id"]
        if not items["pageInfo"]["hasNextPage"]:
            break
        cursor = items["pageInfo"]["endCursor"]
    return titles


def add_draft(title):
    d = gql(
        "mutation($p:ID!,$t:String!){ addProjectV2DraftIssue(input:{projectId:$p, title:$t}){ projectItem { id } } }",
        p=PROJECT_ID, t=title,
    )
    return d["addProjectV2DraftIssue"]["projectItem"]["id"]


def set_field(item_id, field_id, *, option=None, text=None):
    value = ""
    if option is not None:
        value = "value:{singleSelectOptionId:%s}" % json.dumps(option)
    elif text is not None:
        value = "value:{text:%s}" % json.dumps(text)
    gql(
        "mutation($p:ID!,$i:ID!,$f:ID!){ updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,%s}){ projectV2Item { id } } }" % value,
        p=PROJECT_ID, i=item_id, f=field_id,
    )


def cleanup_pilot():
    # Delete pilot command issues (titled like tree paths), the converted
    # eoe.sw.base issue, and the broken Product single-select field.
    out = gh(["issue", "list", "--repo", REPO, "--state", "all",
              "--json", "number,title", "--limit", "200"])
    issues = json.loads(out.stdout)
    cmds = [i for i in issues if "/" in i["title"]]
    print(f"deleting {len(cmds)} pilot command issues")
    for i in cmds:
        gh(["issue", "delete", "--repo", REPO, str(i["number"])])
    prod = [i for i in issues if i["title"] == "eoe.sw.base"]
    for i in prod:
        gh(["issue", "delete", "--repo", REPO, str(i["number"])])
        print(f"deleted product issue #{i['number']} eoe.sw.base")
    delete_field(PRODUCT_FIELD)  # broken 21-option single-select from the pilot


def main(argv=None):
    ap = argparse.ArgumentParser(prog="board-flatten.py")
    ap.add_argument("--tree", required=True)
    ap.add_argument("--product", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cleanup-pilot", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.cleanup_pilot:
        cleanup_pilot()
        return

    idb_dir = os.path.join(args.tree, ".recovery", "idb")
    prod = parse_idb(idb_dir)
    if args.product not in prod:
        sys.exit(f"product {args.product!r} not in idb")
    units = prod[args.product]
    if args.limit:
        units = units[: args.limit]

    fields = ensure_fields(sorted(prod.keys()))
    sub = fields[SUBSYSTEM_FIELD]
    product_f = fields[PRODUCT_FIELD]
    src = fields["src"]
    titles = project_item_titles()

    bucket = subsystem(args.product)
    bucket = bucket if bucket in sub["options"] else "misc"

    to_create = [u for u in units if u not in titles]
    if args.dry_run:
        print(f"[dry-run] create {len(to_create)} drafts: "
              f"Subsystem={bucket}, Product={args.product}, src=full")
        for u in to_create[:10]:
            print("   -", u)
        return

    done = 0
    for u in to_create:
        item_id = add_draft(u)
        set_field(item_id, sub["id"], option=sub["options"][bucket])
        set_field(item_id, product_f["id"], text=args.product)
        set_field(item_id, src["id"], option=src["options"]["full"])
        done += 1
        print(f"  [{done}/{len(to_create)}] {u}")

    print(f"done: {args.product} -> {done} command items (subsystem={bucket})")


if __name__ == "__main__":
    main()
