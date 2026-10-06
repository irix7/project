#!/usr/bin/env python3
"""Logical-offset reader and record finder for EFS inode content.

Works on any inode's byte stream regardless of its extent layout:
``read_logical`` reads a byte range across extents without loading the whole
file, and ``find_record`` stream-scans a large archive for a byte pattern (an
IRIX installer record path) in 4 MiB windows. Both take the ``EFS`` object
from ``efs.py`` and an inode info dict from ``EFS.inode()``.
"""

CHUNK = 1 << 22


def read_logical(efs, info, logical_off, n):
    """Read n bytes at a logical file offset, spanning extents."""
    out = bytearray()
    exts = efs.all_extents(info)
    while n > 0:
        ext = None
        for entry in exts:
            _, bn, ln, of = entry
            if of * 512 <= logical_off < (of + ln) * 512:
                ext = entry
                break
        if ext is None:
            break
        _, bn, ln, of = ext
        phys = (efs.fs_start + bn) * 512 + (logical_off - of * 512)
        take = min(n, (of + ln) * 512 - logical_off)
        out += efs.pread(phys, take)
        logical_off += take
        n -= take
    return bytes(out)


def find_record(efs, info, path):
    """Return the absolute byte offset of a record path, or None if absent."""
    needle = path.encode()
    total = info['size']
    overlap = len(needle) + 4
    off = 0
    tail = b''
    while off < total:
        chunk = read_logical(efs, info, off, min(CHUNK, total - off))
        if not chunk:
            break
        buf = tail + chunk
        idx = buf.find(needle)
        if idx >= 0:
            abs_off = off - len(tail) + idx
            if abs_off >= 2:
                return abs_off
        tail = buf[-overlap:]
        off += len(chunk)
    return None
