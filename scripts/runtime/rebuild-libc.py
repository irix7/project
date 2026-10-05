#!/usr/bin/env python3
"""Rebuild the tree's libc and libm with the GCC 16.2 cross (issue #9).

The runtime proof: compile the IRIX tree's o32 nonshared C library and maths
sources with the project's cross, archive them, link the smoke program
statically against the rebuilt libc, run it on the guest and diff its stdout
against the recorded oracle output (ADR-0006's deferred static proof).

Source selection comes from the tree's own smake leaf makefiles
(scripts/runtime/smake.py), and the MIPSpro-only constructs those sources use
are translated in a stream (scripts/runtime/compat.py); the tree itself is
never edited (ADR-0005). The static link uses the tree's own csu startfiles,
so it needs no SGI runtime at all.

The driver is a host build until the final phase; the guest transaction runs
under the rig's shared guest lock, like smoke.sh and hinv-reference.sh, and
its stdout is diffed against the oracle's recorded output.

Run inside the flake devshell (host cc and GNU m4 are the generation tools):

    nix develop --command python3 scripts/runtime/rebuild-libc.py \
        --tree /home/matt/projects/irix-6.5.7m-src

Every artefact stays under --out (default .scratch/runtime); nothing built
from the tree is committed (ADR-0001).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import compat  # noqa: E402
import smake  # noqa: E402

TARGET = "mips-sgi-irix6.5"
DEFAULT_LIBRARY = "libc_32_M2_ns.a"
DEFAULT_OBJECT_STYLE = "32_M2"

# The static proof's ABI: o32/mips2, the Indy's native environment, matching
# libc_32_M2_ns.a. The assembler is driven at mips3 because the tree's maths
# sources carry 64-bit float instructions (dmfc1) the o32 mips2 assembler
# rejects but the R4400 executes (ADR-0003's MIPS III target).
C_ARCH = ["-mabi=32", "-mips2", "-G", "0"]
ASM_ARCH = ["-mabi=32", "-mips3", "-G", "0"]

# o32 has no 128-bit long double: the tree's fpparts.h hides _ldblval under
# _MIPS_SIM_ABI32, and the released o32 libc/libm export none of the quad
# symbols. The maths leaf still enumerates its QUAD_WORD_CFILES/ASFILES for
# this style, so the driver drops every member those variables name; they
# cannot exist in an o32 archive. The rest of the maths the o32 library
# carries (sqrt, arithmetic, conversions) is unaffected.
O32_QUAD_REASON = (
    "128-bit long double is absent on o32 (fpparts.h); "
    "the released o32 libc carries no quad symbols"
)

# Tree rules that are per-object, not per-directory. strings/Makefile builds
# bcmp.o from bcmp.s with -DISBCMP (defining _bcmp) while the phantom memcmp.s
# entry builds memcmp.o from the same source without it (defining memcmp).
PER_FILE_DEFINES = {
    "strings/bcmp.s": ["-DISBCMP"],
}


def o32_quad_excludes(variables: dict) -> dict:
    """The o32 exclusions the maths leaf's own quad variables name."""
    excludes = {}
    math = variables.get("math", {})
    for key in ("QUAD_WORD_CFILES", "QUAD_WORD_ASFILES"):
        for name in math.get(key, "").split():
            excludes[f"math/{name}"] = O32_QUAD_REASON
    return excludes

# Source-upload errata: defects in the release's copy that stop any compiler,
# not MIPSpro/GCC divergences. Each is translated in the build's copy. The
# unterminated-statement repair recognises the construct by syntax alone, so
# no tree source text is carried in the repository (ADR-0001).
ERRATA = {
    "sys/mq_open.c": lambda text: compat.retarget_knr_definition(
        text, "mq_open", "__mq_open_impl"
    ),
    "locale/sgi_ffmtmsg.c": compat.fix_unterminated_statement,
}

# The generated sources and where the generator writes them.
GENERATED_SOURCES = {
    "gen/errlst.c": "gen",
    "gen/new_list.c": "gen",
    "iconv/iconv_converter.c": "iconv",
    "iconv/stdlib_conv.c": "iconv",
}
WRAP_SOURCE = "iconv/stdlib_conv_wrap.c"
# strings/Makefile names memcmp.s in ASFILES but only bcmp.s exists: the
# memcmp.o rule assembles bcmp.s unchanged. See PER_FILE_DEFINES for bcmp.o.
MEMCMP_PHANTOM = "strings/memcmp.s"


class BuildError(Exception):
    pass


def log(message: str) -> None:
    print(f"[runtime] {message}", flush=True)


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], **kwargs)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError(message)


# ---------------------------------------------------------------------------
# Flag translation
# ---------------------------------------------------------------------------


def gcc_include_dir(prefix: Path) -> str:
    gcc = prefix / "bin" / f"{TARGET}-gcc"
    proc = run([gcc, "-print-file-name=include"], capture_output=True, text=True)
    require(proc.returncode == 0, f"cannot ask {gcc} for its include dir")
    return proc.stdout.strip()


def manifest_vars(rows) -> dict:
    """Per-subdirectory variable rows from the manifest."""
    result: dict = {}
    for kind, subdir, name, value in rows:
        if kind == "var":
            result.setdefault(subdir, {})[name] = value
    return result


def map_tree_path(tree: Path, sysroot: Path, path: str) -> str:
    """Translate a makefile include/search path into this host's tree."""
    if path.startswith("/usr/include"):
        candidate = tree / "irix" / path.lstrip("/")
        if candidate.exists():
            return str(candidate)
        return str(sysroot / path.lstrip("/"))
    return path


def local_flags(tree: Path, sysroot: Path, values: dict, keys) -> list:
    flags = []
    for key in keys:
        for word in values.get(key, "").split():
            if word.startswith("-I"):
                flags.append("-I" + map_tree_path(tree, sysroot, word[2:]))
            elif word.startswith("-D"):
                flags.append(word)
    return flags


# The version and library defines every source in the build shares; the C and
# assembler paths must see exactly the same macro world.
LIBRARY_DEFINES = [
    "-D_SGI_MP_SOURCE",
    "-D_LIBC_NONSHARED",
    "-DNDEBUG",
    "-D_SGI_COMPILING_LIBC",
    "-D_SYSTYPE_SVR4",
    "-U__MATH_HAS_NO_SIDE_EFFECTS",
    "-U__INLINE_INTRINSICS",
    "-D__return_address=__builtin_return_address(0)",
]


def include_chain(tree: Path, sysroot: Path, gccdir: str) -> list:
    """GCC's own headers first, then the tree, then the captured sysroot.

    SGI's stdarg.h in front of GCC's breaks the compiler, so the chain is
    explicit under -nostdinc rather than left to search order.
    """
    return [
        "-nostdinc",
        f"-isystem{gccdir}",
        f"-isystem{tree}/irix/include",
        f"-isystem{tree}/irix/usr/include",
        f"-isystem{sysroot}/usr/include",
        f"-idirafter{tree}/irix/kern",
        f"-idirafter{tree}/irix/kern/bsd",
    ]


def c_flags(tree: Path, sysroot: Path, gccdir: str) -> list:
    return [
        f"--sysroot={sysroot}",
        *C_ARCH,
        "-O",
        "-std=gnu89",
        "-w",
        *LIBRARY_DEFINES,
        *include_chain(tree, sysroot, gccdir),
    ]


def subdir_c_flags(tree: Path, sysroot: Path, values: dict) -> list:
    flags = local_flags(
        tree, sysroot, values, ("LCDEFS", "LCINCS", "SUBDIR_CDEFS", "SUBDIR_CINCS")
    )
    return flags


def subdir_as_flags(tree: Path, sysroot: Path, values: dict) -> list:
    return local_flags(
        tree, sysroot, values, ("LASDEFS", "LASINCS", "SUBDIR_ASINCS")
    )


# ---------------------------------------------------------------------------
# Generation phase
# ---------------------------------------------------------------------------


def host_cc() -> str:
    for candidate in (os.environ.get("CC"), "gcc", "cc"):
        if candidate and shutil.which(candidate):
            return candidate
    raise BuildError("no host C compiler (run inside the flake devshell)")


def host_tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise BuildError(f"{name} not found (run inside the flake devshell)")
    return found


def generate(tree: Path, out: Path) -> dict:
    """Produce the tree's generated libc sources with host tools."""
    src = tree / "irix" / "lib" / "libc" / "src"
    gen = out / "gen"
    (gen / "computed_include").mkdir(parents=True, exist_ok=True)
    (gen / "iconv").mkdir(parents=True, exist_ok=True)
    (gen / "gen").mkdir(parents=True, exist_ok=True)
    bindir = gen / "bin"
    bindir.mkdir(exist_ok=True)

    cc = host_cc()
    extra_includes = [
        f"-idirafter{tree}/irix/usr/include",
        f"-idirafter{tree}/eoe/include",
    ]

    # computed_include: three small host tools and the headers they emit.
    tools = {
        "mbwcoffs": ["mbwcoffs.c", []],
        "make_table": ["make_table.c", ["-Doserror()=errno"]],
        "matrix": ["matrix.c", []],
    }
    for name, (source, defines) in tools.items():
        cmd = [cc, "-O", *extra_includes, *defines, "-o", bindir / name, src / "computed_include" / source]
        proc = run(cmd, cwd=src / "computed_include", capture_output=True, text=True)
        require(proc.returncode == 0, f"host tool {name} failed:\n{proc.stderr}")

    (gen / "computed_include" / "mbwc_wrap.h").write_bytes(
        run([bindir / "mbwcoffs"], capture_output=True, check=True).stdout
    )
    (gen / "computed_include" / "iconv_top_include.m4").write_bytes(
        run([bindir / "matrix"], capture_output=True, check=True).stdout
    )

    # iconv: GNU m4 expands the templates, including the generated top
    # include; diverts write the symbol and declaration files beside them.
    for item in (src / "iconv").iterdir():
        if item.is_file() and (item.suffix == ".m4" or item.name == "nonshrsyms.syms"):
            shutil.copy(item, gen / "iconv" / item.name)
    m4 = host_tool("m4")
    for template, output in (
        ("iconv_top_src.m4", "iconv_converter.c"),
        ("stdlib_top_src.m4", "stdlib_conv.c"),
    ):
        with open(gen / "iconv" / output, "wb") as handle:
            proc = run(
                [m4, "-B65536", "-D__date__=runtime-rebuild", template],
                cwd=gen / "iconv",
                stdout=handle,
                stderr=subprocess.PIPE,
            )
        require(proc.returncode == 0, f"m4 {template} failed")

    (gen / "iconv" / "symtab.syms").write_text(
        (gen / "iconv" / "nonshrsyms.syms").read_text()
        + (gen / "iconv" / "stdlib.syms").read_text()
        + (gen / "iconv" / "iconv.syms").read_text()
    )
    proc = run(
        [bindir / "make_table", "symtab.syms", "symtab.h"],
        cwd=gen / "iconv",
        capture_output=True,
        text=True,
    )
    require(proc.returncode == 0, f"make_table failed:\n{proc.stderr}")

    # gen: mkerrlist writes errlst.c and new_list.c from the errlist data.
    shutil.copy(src / "gen" / "errlist", gen / "gen" / "errlist")
    proc = run(
        [
            cc,
            "-Doserror()=errno",
            "-o",
            bindir / "mkerrlist",
            src / "gen" / "mkerrlist.c",
        ],
        capture_output=True,
        text=True,
    )
    require(proc.returncode == 0, f"host tool mkerrlist failed:\n{proc.stderr}")
    proc = run([bindir / "mkerrlist", "errlist"], cwd=gen / "gen", capture_output=True, text=True)
    require(proc.returncode == 0, f"mkerrlist failed:\n{proc.stderr}")

    return {
        "gen/errlst.c": gen / "gen" / "errlst.c",
        "gen/new_list.c": gen / "gen" / "new_list.c",
        "iconv/iconv_converter.c": gen / "iconv" / "iconv_converter.c",
        "iconv/stdlib_conv.c": gen / "iconv" / "stdlib_conv.c",
    }


# ---------------------------------------------------------------------------
# Compile phase
# ---------------------------------------------------------------------------


def filtered_source(out: Path, subdir: str, name: str) -> Path:
    return out / "src" / subdir / name


def compile_one(
    row,
    tree: Path,
    sysroot: Path,
    gccdir: str,
    shims: Path,
    out: Path,
    generated: dict,
    gcc: Path,
    variables: dict,
    excludes: dict,
    print_commands: bool,
) -> tuple:
    kind, subdir, name, _ = row
    key = f"{subdir}/{name}"
    if key in excludes:
        return ("excluded", subdir, name, excludes[key])
    if key == WRAP_SOURCE:
        return ("special", subdir, name, "linked from stdlib_conv.o and mbwc_wrap.o")
    if key == MEMCMP_PHANTOM:
        source = tree / "irix/lib/libc/src" / subdir / "bcmp.s"
    elif key in generated:
        source = generated[key]
    else:
        source = tree / "irix/lib/libc/src" / subdir / name

    obj = out / "obj" / subdir / (name.rsplit(".", 1)[0] + ".o")
    obj.parent.mkdir(parents=True, exist_ok=True)
    values = variables.get(subdir, {})
    includes = [
        f"-I{tree}/irix/lib/libc/inc",
        f"-I{shims}",
        f"-I{out}/shim",
        f"-iquote{tree}/irix/lib/libc/src/{subdir}",
    ]
    if subdir == "iconv":
        includes.insert(0, f"-I{out}/gen/iconv")

    if kind == "c":
        text = source.read_text()
        if key in ERRATA:
            text = ERRATA[key](text)
        text = compat.rewrite_c_source(text)
        filtered = filtered_source(out, subdir, name)
        filtered.parent.mkdir(parents=True, exist_ok=True)
        filtered.write_text(text)
        cmd = [
            gcc,
            *c_flags(tree, sysroot, gccdir),
            *includes,
            *subdir_c_flags(tree, sysroot, values),
            "-c",
            filtered,
            "-o",
            obj,
        ]
        if print_commands:
            print("compile:", " ".join(str(c) for c in cmd))
        else:
            proc = run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                (out / "logs").mkdir(exist_ok=True)
                (out / "logs" / f"{subdir}_{name}.log").write_text(proc.stderr)
                return ("fail", subdir, name, proc.stderr.splitlines()[-1] if proc.stderr else "")
        return ("ok", subdir, name, obj)

    # Assembly: preprocess, translate MIPSpro syntax, then assemble.
    preprocessed = out / "asm" / subdir / name
    preprocessed.parent.mkdir(parents=True, exist_ok=True)
    asm_includes = [f"-I{tree}/irix/lib/libc/inc", f"-I{shims}", f"-I{out}/shim"]
    if subdir == "iconv":
        asm_includes.insert(0, f"-I{out}/gen/iconv")
    cpp_cmd = [
        gcc,
        f"--sysroot={sysroot}",
        *ASM_ARCH,
        "-w",
        "-E",
        "-x",
        "assembler-with-cpp",
        "-traditional-cpp",
        *LIBRARY_DEFINES,
        *PER_FILE_DEFINES.get(key, []),
        *asm_includes,
        *include_chain(tree, sysroot, gccdir),
        *subdir_as_flags(tree, sysroot, values),
        source,
        "-o",
        preprocessed,
    ]
    as_cmd = [
        gcc,
        f"--sysroot={sysroot}",
        *ASM_ARCH,
        "-w",
        "-x",
        "assembler",
        "-c",
        preprocessed,
        "-o",
        obj,
    ]
    if print_commands:
        print("preprocess:", " ".join(str(c) for c in cpp_cmd))
        print("assemble:", " ".join(str(c) for c in as_cmd))
    else:
        proc = run(cpp_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return ("fail", subdir, name, f"cpp: {proc.stderr.strip()}")
        preprocessed.write_text(compat.rewrite_asm_source(preprocessed.read_text()))
        proc = run(as_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            (out / "logs").mkdir(exist_ok=True)
            (out / "logs" / f"{subdir}_{name}.log").write_text(proc.stderr)
            return ("fail", subdir, name, proc.stderr.splitlines()[-1] if proc.stderr else "")
    return ("ok", subdir, name, obj)


def build_csu(tree: Path, sysroot: Path, gccdir: str, shims: Path, out: Path, gcc: Path, ld: Path) -> dict:
    """Assemble the tree's nonshared startfiles (libc/src/csu)."""
    csu = tree / "irix/lib/libc/src/csu"
    work = out / "csu"
    work.mkdir(parents=True, exist_ok=True)

    def assemble(source: Path, output: Path, *defines: str) -> None:
        pre = work / (source.stem + ".s")
        proc = run(
            [
                gcc,
                f"--sysroot={sysroot}",
                *ASM_ARCH,
                "-w",
                "-E",
                "-x",
                "assembler-with-cpp",
                "-traditional-cpp",
                "-D_LIBC_NONSHARED",
                f"-I{tree}/irix/lib/libc/inc",
                f"-I{tree}/irix/usr/include",
                *defines,
                source,
                "-o",
                pre,
            ],
            capture_output=True,
            text=True,
        )
        require(proc.returncode == 0, f"cpp {source.name}: {proc.stderr.strip()}")
        pre.write_text(compat.rewrite_asm_source(pre.read_text()))
        proc = run(
            [
                gcc,
                f"--sysroot={sysroot}",
                *ASM_ARCH,
                "-mxgot",
                "-w",
                "-x",
                "assembler",
                "-c",
                pre,
                "-o",
                output,
            ],
            capture_output=True,
            text=True,
        )
        require(proc.returncode == 0, f"as {source.name}: {proc.stderr.strip()}")

    crt1_text = work / "crt1text.o"
    crt1_init = work / "crt1tinit.o"
    crtn_init = work / "crtninit.o"
    assemble(csu / "crt1text.s", crt1_text, "-DCRT0")
    assemble(csu / "crt1tinit.s", crt1_init)
    assemble(csu / "crtninit.s", crtn_init)

    crt1 = work / "crt1.o"
    proc = run([ld, "-m", "elf32bsmip", "-r", "-o", crt1, crt1_text, crt1_init], capture_output=True, text=True)
    require(proc.returncode == 0, f"ld -r crt1.o: {proc.stderr.strip()}")
    crtn = work / "crtn.o"
    proc = run([ld, "-m", "elf32bsmip", "-r", "-o", crtn, crtn_init], capture_output=True, text=True)
    require(proc.returncode == 0, f"ld -r crtn.o: {proc.stderr.strip()}")
    return {"crt1.o": crt1, "crtn.o": crtn}


# ---------------------------------------------------------------------------
# Guest phase
# ---------------------------------------------------------------------------


# The same guest transaction shape as smoke.sh: mkdir, put, run through sh
# with the program's status written to a file, get all three streams. Kept as
# a template here so this proof owns its guest files and cannot disturb the
# smoke harness's.
GUEST_TXN = r"""#!/usr/bin/env bash
set -euo pipefail

binary=$1
guest_bin=$2
host_out=$3
host_err=$4
host_status=$5
timeout=$6
repo_root=$7

# shellcheck source=scripts/rig/lib.sh
source "$repo_root/scripts/rig/lib.sh"

ic -q run "mkdir -p /tmp/runtime" --timeout "$timeout" >/dev/null
ic -q put "$binary" --to "$guest_bin" --timeout "$timeout" >/dev/null
run_status=0
ic -q run "sh -c 'chmod +x $guest_bin; $guest_bin > $guest_bin.stdout 2> $guest_bin.stderr; echo \$? > $guest_bin.status'" \
	--timeout "$timeout" >/dev/null || run_status=$?
ic -q get "$guest_bin.stdout" --to "$host_out" --timeout "$timeout" >/dev/null || true
ic -q get "$guest_bin.stderr" --to "$host_err" --timeout "$timeout" >/dev/null || true
ic -q get "$guest_bin.status" --to "$host_status" --timeout "$timeout" >/dev/null || true
[ "$run_status" -eq 0 ] || exit 90
exit 0
"""


def run_on_guest(binary: Path, out: Path, timeout: int) -> tuple:
    txn = out / "guest-txn.sh"
    txn.write_text(GUEST_TXN)
    host_out = out / "hello.stdout"
    host_err = out / "hello.stderr"
    host_status = out / "hello.status"
    guest_bin = "/tmp/runtime/hello-static"
    # lib.sh's shared guest lock, same path and timeout precedence, so this
    # serialises with smoke.sh and the reference builds.
    rig_dir = os.environ.get("IRIX_RIG_DIR", "/mnt/europa/sgi-toolchain-scratch/rig")
    lock = os.environ.get("RIG_GUEST_LOCK") or f"{rig_dir}/guest.lock"
    proc = run(
        [
            "flock",
            "-w",
            os.environ.get("RIG_GUEST_LOCK_TIMEOUT", "3600"),
            lock,
            "bash",
            txn,
            binary,
            guest_bin,
            host_out,
            host_err,
            host_status,
            str(timeout),
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BuildError(f"guest transaction failed ({proc.returncode}):\n{proc.stderr}")
    status = host_status.read_text().strip()
    return status, host_out, host_err


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", required=True, help="IRIX 6.5.7m source tree root")
    parser.add_argument("--prefix", default=str(REPO_ROOT / ".scratch/toolchain-16.2.0/prefix"))
    parser.add_argument("--sysroot", default=None, help="default: the cross's -print-sysroot")
    parser.add_argument("--out", default=str(REPO_ROOT / ".scratch/runtime"))
    parser.add_argument("--library", default=DEFAULT_LIBRARY)
    parser.add_argument("--object-style", default=DEFAULT_OBJECT_STYLE)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--no-run", action="store_true", help="build and link only")
    parser.add_argument("--print-commands", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    started = time.time()
    tree = Path(args.tree).resolve()
    prefix = Path(args.prefix).resolve()
    out = Path(args.out).resolve()
    jobs = max(1, args.jobs)

    gcc = prefix / "bin" / f"{TARGET}-gcc"
    ld = prefix / "bin" / f"{TARGET}-ld"
    readelf = prefix / "bin" / f"{TARGET}-readelf"
    ar = prefix / "bin" / f"{TARGET}-ar"
    for tool in (gcc, ld, readelf, ar):
        require(tool.exists(), f"cross tool not found: {tool}")

    sysroot = Path(args.sysroot).resolve() if args.sysroot else Path(
        run([gcc, "-print-sysroot"], capture_output=True, text=True).stdout.strip()
    )
    require((sysroot / "usr/include").is_dir(), f"sysroot has no headers: {sysroot}")
    require((tree / "irix/lib/libc/src/Makefile").is_file(), f"not an IRIX tree: {tree}")

    if not args.print_commands:
        out.mkdir(parents=True, exist_ok=True)
        (out / "shim" / "sys").mkdir(parents=True, exist_ok=True)
        shutil.copy(
            tree / "irix/kern/bsd/sesmgr/t6api_private.h",
            out / "shim" / "sys" / "t6api_private.h",
        )

    gccdir = gcc_include_dir(prefix)
    shims = HERE / "include"

    rows = list(smake.manifest(str(tree), args.library, args.object_style))
    variables = manifest_vars(rows)
    sources = [row for row in rows if row[0] in ("c", "s")]
    # The makefile builds mbwc_wrap.o by its own explicit rule (it is not in
    # ASFILES in the WITH_STYLE branch), so the driver adds it whenever the
    # variant links stdlib_conv_wrap.o from it.
    if any(row[2] == "stdlib_conv_wrap.c" for row in sources):
        sources.append(("c", "iconv", "stdlib_conv.c", ""))
        sources.append(("s", "iconv", "mbwc_wrap.s", ""))
    excludes = o32_quad_excludes(variables)
    log(f"{len(sources)} sources for {args.library} ({args.object_style}); "
        f"{len(excludes)} quad sources excluded for o32")

    generated = {}
    if not args.print_commands:
        log("generating the tree's generated sources")
        generated = generate(tree, out)

    log(f"compiling with {jobs} jobs")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [
            pool.submit(
                compile_one,
                row,
                tree,
                sysroot,
                gccdir,
                shims,
                out,
                generated,
                gcc,
                variables,
                excludes,
                args.print_commands,
            )
            for row in sources
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    failures = [r for r in results if r[0] == "fail"]
    excluded = [r for r in results if r[0] == "excluded"]
    if failures:
        for status, subdir, name, detail in sorted(failures):
            print(f"FAIL {subdir}/{name}: {detail}", file=sys.stderr)
        raise BuildError(f"{len(failures)} sources failed to build")
    log(f"compiled {sum(1 for r in results if r[0] == 'ok')} objects; "
        f"{len(excluded)} excluded by the o32 boundary; "
        f"{sum(1 for r in results if r[0] == 'special')} special")
    if args.print_commands:
        return 0

    # Special objects: the iconv wrap links stdlib_conv.o with mbwc_wrap.o.
    objects = [r[3] for r in results if r[0] == "ok"]
    math_objects = [
        r[3] for r in results if r[0] == "ok" and r[1] == "math"
    ]
    iconv_dir = out / "obj" / "iconv"
    stdlib_conv = iconv_dir / "stdlib_conv.o"
    mbwc_wrap = iconv_dir / "mbwc_wrap.o"
    wrap = iconv_dir / "stdlib_conv_wrap.o"
    proc = run([ld, "-m", "elf32bsmip", "-r", "-o", wrap, mbwc_wrap, stdlib_conv], capture_output=True, text=True)
    require(proc.returncode == 0, f"ld -r stdlib_conv_wrap.o: {proc.stderr.strip()}")
    intermediates = {stdlib_conv, mbwc_wrap}
    objects = [obj for obj in objects if Path(obj) not in intermediates]
    objects.append(wrap)

    libc = out / "libc.a"
    if libc.exists():
        libc.unlink()
    proc = run([ar, "rc", libc, *objects], capture_output=True, text=True)
    require(proc.returncode == 0, f"ar libc.a: {proc.stderr.strip()}")
    libm = out / "libm.a"
    if libm.exists():
        libm.unlink()
    proc = run([ar, "rc", libm, *math_objects], capture_output=True, text=True)
    require(proc.returncode == 0, f"ar libm.a: {proc.stderr.strip()}")
    log(f"libc.a: {len(objects)} members; libm.a: {len(math_objects)} members")

    startfiles = build_csu(tree, sysroot, gccdir, shims, out, gcc, ld)

    # Static hello: the tree's startfiles and archives only, with libgcc.
    hello_obj = out / "hello.o"
    source = REPO_ROOT / "oracle" / "hello.c"
    proc = run(
        [
            gcc,
            f"--sysroot={sysroot}",
            *C_ARCH,
            "-O",
            "-std=gnu89",
            "-c",
            source,
            "-o",
            hello_obj,
        ],
        capture_output=True,
        text=True,
    )
    require(proc.returncode == 0, f"hello compile failed:\n{proc.stderr}")
    libgcc = Path(
        run([gcc, "-print-libgcc-file-name"], capture_output=True, text=True).stdout.strip()
    )
    rld_stub = out / "static-rld-stub.o"
    proc = run(
        [
            gcc,
            f"--sysroot={sysroot}",
            *C_ARCH,
            "-w",
            "-c",
            HERE / "static-rld-stub.c",
            "-o",
            rld_stub,
        ],
        capture_output=True,
        text=True,
    )
    require(proc.returncode == 0, f"rld stub compile failed:\n{proc.stderr}")
    hello = out / "hello-static"
    link_cmd = [
        gcc,
        f"--sysroot={sysroot}",
        *C_ARCH,
        "-static",
        "-nostdlib",
        "-o",
        hello,
        startfiles["crt1.o"],
        hello_obj,
        rld_stub,
        "-Wl,--start-group",
        libm,
        libc,
        libgcc,
        "-Wl,--end-group",
        startfiles["crtn.o"],
    ]
    proc = run(link_cmd, capture_output=True, text=True)
    require(proc.returncode == 0, f"static link failed:\n{proc.stderr}")

    proof = run([readelf, "-h", "-l", "-d", hello], capture_output=True, text=True).stdout
    (out / "readelf.txt").write_text(proof)
    require("INTERP" not in proof, "static binary has a PT_INTERP")
    require("NEEDED" not in proof, "static binary has a NEEDED entry")
    log(f"linked {hello} ({hello.stat().st_size} bytes), no PT_INTERP, no NEEDED")

    if args.no_run:
        log(f"done in {time.time() - started:.1f}s (guest run skipped)")
        return 0

    log("running on the guest")
    status, host_out, host_err = run_on_guest(hello, out, args.timeout)
    if status != "0":
        raise BuildError(f"guest exited {status}:\nstdout:\n{host_out.read_text()}\n"
                         f"stderr:\n{host_err.read_text()}")
    expected = (REPO_ROOT / "scripts/smoke/hello.expected").read_text()
    actual = host_out.read_text()
    if actual != expected:
        raise BuildError(f"stdout mismatch:\nexpected:\n{expected}\nactual:\n{actual}")
    log("guest stdout matches scripts/smoke/hello.expected")
    print(actual, end="")
    log(f"done in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as error:
        print(f"runtime: {error}", file=sys.stderr)
        raise SystemExit(1)
