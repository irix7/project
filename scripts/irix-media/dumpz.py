#!/usr/bin/env python3
"""Dump one installed file from an IRIX dist archive without decompressing in Python.

Streams the compressed record to a ``.Z`` file in bounded chunks, then lets the
system ``gzip -dc`` decode it (the pure-Python LZW decoder is slow on large
streams and can diverge on some dev-CD streams; see extract-sw.py).  If the
decoded payload is an ELF, truncates it to its section-table extent so any
trailing archive framing is dropped.

Usage: python3 dumpz.py <image> <archive-path> <installed-path> [outfile]

Example (the IRIX 6.5 Development Libraries CD ships unstripped debug
libraries inside its dist archives):

    python3 dumpz.py \\
      "/mnt/europa/sgi-mame/IRIX 6.5 Development Libraries June 1998.iso" \\
      /dist/dmedia_dev.sw usr/lib/debug/libdmedia.so /tmp/libdmedia.so
"""
import os
import struct
import subprocess
import sys

import efs
import reader

MAX_CMP = 24 << 20  # cap on the compressed slice read from the archive
CHUNK = 1 << 20


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    image, archive, path = sys.argv[1], sys.argv[2], sys.argv[3]
    out = sys.argv[4] if len(sys.argv) > 4 else os.path.join(
        '/tmp', os.path.basename(path))
    e = efs.EFS(image)
    files = {p: (ft, s, ino) for p, ft, s, ino in e.walk()}
    if archive not in files:
        print("NOSW", archive)
        sys.exit(1)
    info = e.inode(files[archive][2])
    pos = reader.find_record(e, info, path)
    if pos is None:
        print("MISS", path)
        sys.exit(1)
    zfile = out + '.Z'
    total = 0
    with open(zfile, 'wb') as f:
        off = pos + len(path)
        while total < MAX_CMP:
            data = reader.read_logical(e, info, off, CHUNK)
            if not data:
                break
            f.write(data)
            off += len(data)
            total += len(data)
    magic = open(zfile, 'rb').read(2)
    if magic != b'\x1f\x9d':
        print("NOTZ", path, magic)
        sys.exit(1)
    rc = subprocess.run(['gzip', '-dc', zfile], stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL).stdout
    data = rc
    if data[:4] == b'\x7fELF':
        endian = '<' if data[5] == 1 else '>'
        h = struct.unpack_from(endian + 'HHIIIIIHHHHHH', data, 16)
        shoff, shentsize, shnum = h[5], h[10], h[11]
        end = shoff + shentsize * shnum
        for i in range(shnum):
            x = struct.unpack_from(endian + 'IIIIIIIIII', data,
                                   shoff + i * shentsize)
            end = max(end, x[4] + x[5])
        data = data[:end]
    with open(out, 'wb') as f:
        f.write(data)
    print("wrote", out, len(data), "compressed", total)


if __name__ == '__main__':
    main()
