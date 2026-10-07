#!/usr/bin/env python3
"""Board helper for the IRIX 6.5.7m rebuild project.

One interface to the public GitHub Project "IRIX 6.5.7m rebuild"
(irix7/projects/1) for parallel rebuild workers. The board is the single
source of truth for rebuild state; every agent claims an item, works one
field transition, and advances the board in the same session.

This script wraps `gh` (the GitHub CLI). It resolves field and option ids by
name at run time, so it stays correct if the project is re-created. Reads use
the GraphQL API (paginated); writes use `gh project item-edit` with node ids.

Field vocabulary (see docs/agents/rebuild-worker.md):
    src      full | stub | none | missing   (source availability)
    deco     done | not started | blocked   (decompiled, for stub/none)
    IRIX cc  done | in progress | not started | blocked | n/a   (native build)
    IRIX run done | in progress | not started | blocked | n/a   (native run)
    GCC cc   done | in progress | not started | blocked | n/a   (cross build)
    GCC run  done | in progress | not started | blocked | n/a   (cross run)
    Rust     done | in progress | not started | blocked | n/a   (stage two)

Commands:
    frontier [--lane full|stub|none]   list unclaimed items and their next action
    show TITLE                         print one item's full field state
    claim TITLE                        Status -> In Progress, next field -> in progress
    set TITLE FIELD VALUE              set any single-select field
    done TITLE                         Status -> Done
    release TITLE                      abandon: Status -> Todo, in-progress -> not started

Global flags: --owner (default irix7), --project (default 1), --dry-run
(print the writes without executing them).
"""

import argparse
import json
import subprocess
import sys
from collections import Counter

OWNER_DEFAULT = "irix7"
PROJECT_NUMBER_DEFAULT = "1"

# Action fields in lifecycle order, and the option vocabulary each uses.
LIFECYCLE_FIELDS = ["IRIX cc", "IRIX run", "GCC cc", "GCC run"]
ACTION_FIELDS = LIFECYCLE_FIELDS + ["Rust"]
DECO_FIELD = "deco"
SRC_FIELD = "src"

# GraphQL alias -> field name (aliases must be identifier-safe).
READ_ALIASES = {
    "src": "src",
    "deco": "deco",
    "irixcc": "IRIX cc",
    "irixrun": "IRIX run",
    "gcccc": "GCC cc",
    "gccrun": "GCC run",
    "rust": "Rust",
    "status": "Status",
}
# Reverse: alias -> canonical field name.
ALIAS_TO_FIELD = READ_ALIASES
FIELD_TO_ALIAS = {v: k for k, v in READ_ALIASES.items()}


def gh(args, check=True):
    proc = subprocess.run(
        ["gh"] + args, capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.exit(proc.returncode or 1)
    return proc


def project_id(owner, number):
    out = gh(["project", "view", number, "--owner", owner, "--format", "json"])
    return json.loads(out.stdout)["id"]


def field_map(owner, number):
    """Return {field_name: {"id": ..., "options": {option_name: id}}}."""
    out = gh(
        ["project", "field-list", number, "--owner", owner, "--format", "json"]
    )
    data = json.loads(out.stdout)
    result = {}
    for f in data.get("fields", []):
        if f.get("type") != "ProjectV2SingleSelectField":
            continue
        result[f["name"]] = {
            "id": f["id"],
            "options": {o["name"]: o["id"] for o in f.get("options", [])},
        }
    return result


def _alias_selection():
    return "\n".join(
        f'{alias}: fieldValueByName(name: "{name}") '
        f"{{ ... on ProjectV2ItemFieldSingleSelectValue {{ name }} }}"
        for alias, name in READ_ALIASES.items()
    )


def fetch_items(project_node_id):
    """Return list of {id, title, type, fields: {canonical_name: value_or_None}}."""
    nodes = []
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        query = (
            "query {"
            f'  node(id: "{project_node_id}") {{'
            "    ... on ProjectV2 {"
            f"      items(first: 100{after}) {{"
            "        pageInfo { hasNextPage endCursor }"
            "        nodes {"
            "          id"
            "          type"
            "          content { ... on DraftIssue { title } ... on Issue { title } }"
            f"          {_alias_selection()}"
            "        }"
            "      }"
            "    }"
            "  }"
            "}"
        )
        out = gh(["api", "graphql", "-f", "query=" + query])
        data = json.loads(out.stdout)
        if "errors" in data:
            sys.stderr.write(json.dumps(data["errors"], indent=2) + "\n")
            sys.exit(1)
        items = data["data"]["node"]["items"]
        for n in items["nodes"]:
            fields = {}
            for alias, name in READ_ALIASES.items():
                v = (n.get(alias) or {}).get("name")
                fields[name] = v
            nodes.append(
                {
                    "id": n.get("id"),
                    "title": (n.get("content") or {}).get("title"),
                    "type": n.get("type"),
                    "fields": fields,
                }
            )
        pi = items["pageInfo"]
        if not pi["hasNextPage"]:
            break
        cursor = pi["endCursor"]
    return nodes


def _value(item, field):
    return item["fields"].get(field)


TERMINAL = ("done", "n/a")
BUILDABLE_SRC = ("full", "stub", "none")


def _terminal(value):
    return value in TERMINAL


def phase_state(items):
    """Informational board-wide summary (1 native-heavy, 2 cross-heavy, 3 done).

    This is a *report*, not a gate. Each item flows sequentially
    (deco -> IRIX cc -> IRIX run -> GCC cc -> GCC run), but items proceed in
    parallel: an item can start its GCC step the moment its own native
    reference is done, while other items are still being built natively.
    """
    native_pending = 0
    cross_pending = 0
    for item in items:
        if _value(item, "src") not in BUILDABLE_SRC:
            continue
        if not (_terminal(_value(item, "IRIX cc")) and _terminal(_value(item, "IRIX run"))):
            native_pending += 1
        elif not (_terminal(_value(item, "GCC cc")) and _terminal(_value(item, "GCC run"))):
            cross_pending += 1
    if native_pending:
        return 1
    if cross_pending:
        return 2
    return 3


def next_action(item):
    """Return (field_name, value) of the next transition, or (None, reason).

    Per-item sequential flow; the board as a whole is parallel.
    """
    src = _value(item, "src")
    status = _value(item, "Status")

    if status in ("In Progress", "Done"):
        return None, f"already claimed/closed (Status={status})"

    if src == "missing":
        return None, "src=missing: needs source restoration; mark fields n/a and open an issue"
    if src not in BUILDABLE_SRC:
        return None, f"src unset/unknown ({src!r}); set src first"

    if src in ("stub", "none") and _value(item, "deco") != "done":
        return "deco", "in progress"
    if not _terminal(_value(item, "IRIX cc")):
        return "IRIX cc", "in progress"
    if not _terminal(_value(item, "IRIX run")):
        return "IRIX run", "in progress"
    if not _terminal(_value(item, "GCC cc")):
        return "GCC cc", "in progress"
    if not _terminal(_value(item, "GCC run")):
        return "GCC run", "in progress"
    if not _terminal(_value(item, "Rust")):
        return "Rust", "n/a"
    return None, "all lanes complete"


def _match(items, title):
    """Resolve TITLE to exactly one item by exact or unique substring match."""
    exact = [i for i in items if i["title"] == title]
    if len(exact) == 1:
        return exact[0]
    subs = [i for i in items if i["title"] and title in i["title"]]
    if len(subs) == 1:
        return subs[0]
    if len(subs) == 0:
        sys.exit(f"board: no item matches {title!r}")
    names = "\n  ".join(i["title"] for i in subs[:20])
    sys.exit(f"board: {title!r} matches {len(subs)} items:\n  {names}")


def _edit(owner, number, proj_id, fields, item_id, field, option, dry_run):
    """Set one single-select field on one item."""
    if field not in fields:
        sys.exit(f"board: unknown field {field!r} (single-select fields: {', '.join(sorted(fields))})")
    if option not in fields[field]["options"]:
        opts = ", ".join(sorted(fields[field]["options"]))
        sys.exit(f"board: {field!r} has no option {option!r} (choose from {opts})")
    cmd = [
        "project", "item-edit",
        "--id", item_id,
        "--project-id", proj_id,
        "--field-id", fields[field]["id"],
        "--single-select-option-id", fields[field]["options"][option],
        "--format", "json",
    ]
    if dry_run:
        print(f"[dry-run] gh " + " ".join(cmd))
        return
    proc = gh(cmd)
    if proc.returncode != 0:
        sys.exit(proc.returncode)


def cmd_frontier(args, items):
    lane = args.lane
    shown = 0
    for item in sorted(items, key=lambda i: (i["title"] or "")):
        if not item["title"]:
            continue
        src = _value(item, "src")
        if lane and src != lane:
            continue
        status = _value(item, "Status")
        if status in ("In Progress", "Done"):
            continue
        field, _ = next_action(item)
        if field is None:
            continue
        print(f"{item['title']}\t{src or '-'}\t{status or '-'}\t-> {field}\t{item['id']}")
        shown += 1
    if shown == 0:
        print("(no claimable items)")
    return 0


def _render(item):
    title = item["title"] or "(untitled)"
    lines = [f"{title}  ({item['id']})  type={item['type']}"]
    for field in ["src", "deco", "IRIX cc", "IRIX run", "GCC cc", "GCC run", "Rust", "Status"]:
        v = _value(item, field)
        lines.append(f"  {field:<10} {v if v is not None else '(unset)'}")
    return "\n".join(lines)


def cmd_show(args, items):
    item = _match(items, args.title)
    print(_render(item))
    field, reason = next_action(item)
    if field:
        print(f"  next -> set {field!r} to {reason!r}")
    else:
        print(f"  next -> {reason}")
    return 0


def cmd_claim(args, items, owner, number, proj_id, fields):
    item = _match(items, args.title)
    field, value = next_action(item)
    if field is None:
        sys.exit(f"board: cannot claim {item['title']}: {value}")
    _edit(owner, number, proj_id, fields, item["id"], "Status", "In Progress", args.dry_run)
    if field in fields:
        _edit(owner, number, proj_id, fields, item["id"], field, value, args.dry_run)
    print(f"claimed {item['title']}: Status -> In Progress, {field} -> {value}")
    return 0


def cmd_set(args, items, owner, number, proj_id, fields):
    item = _match(items, args.title)
    _edit(owner, number, proj_id, fields, item["id"], args.field, args.value, args.dry_run)
    print(f"set {item['title']}: {args.field} -> {args.value}")
    return 0


def cmd_done(args, items, owner, number, proj_id, fields):
    item = _match(items, args.title)
    _edit(owner, number, proj_id, fields, item["id"], "Status", "Done", args.dry_run)
    print(f"done {item['title']}: Status -> Done")
    return 0


def cmd_advance(args, items, owner, number, proj_id, fields):
    item = _match(items, args.title)
    target = None
    for field in ["deco"] + LIFECYCLE_FIELDS + ["Rust"]:
        if _value(item, field) == "in progress":
            target = field
            break
    if target is None:
        sys.exit(f"board: {item['title']} has no in-progress field to advance")
    value = args.value or ("n/a" if target == "Rust" else "done")
    _edit(owner, number, proj_id, fields, item["id"], target, value, args.dry_run)
    _edit(owner, number, proj_id, fields, item["id"], "Status", "Todo", args.dry_run)
    print(f"advanced {item['title']}: {target} -> {value}, Status -> Todo")
    return 0


def cmd_status(args, items):
    ph = phase_state(items)
    names = {1: "native-heavy", 2: "cross-heavy", 3: "complete"}
    src_counts = Counter(_value(i, "src") for i in items)
    pending = {"IRIX cc": 0, "IRIX run": 0, "GCC cc": 0, "GCC run": 0}
    for item in items:
        if _value(item, "src") not in BUILDABLE_SRC:
            continue
        for f in pending:
            if not _terminal(_value(item, f)):
                pending[f] += 1
    deco_pending = sum(
        1 for i in items
        if _value(i, "src") in ("stub", "none") and _value(i, "deco") != "done"
    )
    print(f"board summary (activity: {names[ph]})")
    print(f"  items: {len(items)}  src full={src_counts.get('full', 0)} "
          f"stub={src_counts.get('stub', 0)} none={src_counts.get('none', 0)} "
          f"missing={src_counts.get('missing', 0)}")
    print(f"  deco (stub/none) remaining: {deco_pending}")
    for f in ("IRIX cc", "IRIX run", "GCC cc", "GCC run"):
        print(f"  {f:<9} remaining: {pending[f]}")
    return 0


def cmd_release(args, items, owner, number, proj_id, fields):
    item = _match(items, args.title)
    _edit(owner, number, proj_id, fields, item["id"], "Status", "Todo", args.dry_run)
    for field in ["deco"] + ACTION_FIELDS:
        if _value(item, field) == "in progress" and field in fields:
            _edit(owner, number, proj_id, fields, item["id"], field, "not started", args.dry_run)
    print(f"released {item['title']}: Status -> Todo, in-progress fields -> not started")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="board.py", description="Board helper for the IRIX rebuild project."
    )
    parser.add_argument("--owner", default=OWNER_DEFAULT)
    parser.add_argument("--project", default=PROJECT_NUMBER_DEFAULT)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true",
                        help="print write commands without executing them")

    p = sub.add_parser("frontier", help="list claimable items")
    p.add_argument("--lane", choices=["full", "stub", "none", "missing"])

    p = sub.add_parser("show", help="show one item's field state")
    p.add_argument("title")

    p = sub.add_parser("status", help="board-wide summary of remaining work")

    p = sub.add_parser("claim", help="claim an item (Status -> In Progress)",
                       parents=[common])
    p.add_argument("title")

    p = sub.add_parser("advance", help="finish the in-progress step and hand back",
                       parents=[common])
    p.add_argument("title")
    p.add_argument("--value", help="terminal value (default done, or n/a for Rust)")

    p = sub.add_parser("set", help="set one single-select field", parents=[common])
    p.add_argument("title")
    p.add_argument("field")
    p.add_argument("value")

    p = sub.add_parser("done", help="mark an item done (Status -> Done)",
                       parents=[common])
    p.add_argument("title")

    p = sub.add_parser("release", help="abandon a claim", parents=[common])
    p.add_argument("title")

    args = parser.parse_args(argv)

    proj_id = project_id(args.owner, args.project)
    fields = field_map(args.owner, args.project)
    items = fetch_items(proj_id)

    if args.command == "frontier":
        return cmd_frontier(args, items)
    if args.command == "show":
        return cmd_show(args, items)
    if args.command == "status":
        return cmd_status(args, items)
    if args.command == "claim":
        return cmd_claim(args, items, args.owner, args.project, proj_id, fields)
    if args.command == "advance":
        return cmd_advance(args, items, args.owner, args.project, proj_id, fields)
    if args.command == "set":
        return cmd_set(args, items, args.owner, args.project, proj_id, fields)
    if args.command == "done":
        return cmd_done(args, items, args.owner, args.project, proj_id, fields)
    if args.command == "release":
        return cmd_release(args, items, args.owner, args.project, proj_id, fields)
    return 1


if __name__ == "__main__":
    sys.exit(main())
