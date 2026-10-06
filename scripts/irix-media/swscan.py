#!/usr/bin/env python3
"""Scan IRIX inst-image records inside an EFS image, read-only.

Lists the plaintext installed paths carried by ``.sw``/``.src``/``*_hdr``
archive files without decompressing the LZW payloads. Two record shapes are
recognised:

  * the ``im001`` header record at the front of an archive, and
  * the concatenated length-framed records that follow it — a ``\\x00`` byte
    plus an unsigned 8-bit path length (or a big-endian u16), the installed
    path, then the payload, which usually starts with the ``0x1f 0x9d``
    (Unix compress) magic but may be stored raw.

Records are only accepted when the framed path is printable and contains a
slash, which rejects the byte sequences that merely resemble framing inside
compressed data. Writes the path list to /tmp only.

Usage: python3 swscan.py <image>
"""
import os
import re
import sys

import efs
import reader

REC = b'im001'
HDR = re.compile(rb'im001V\d{3}P\d{2}')
MAGIC = b'\x1f\x9d'
CHUNK = 4 * 1024 * 1024
MAXPATH = 512
TAIL = MAXPATH + 8
CONTEXT = MAXPATH + 4


def _walk_records(e, info):
    """Yield (path, payload_len) for each record in one archive."""
    total = info['size']
    tail = b''
    off = 0
    seen = set()
    recs = {}

    def emit(buf, base):
        # im001 header records: im001V###P## <3-byte BE length> <path>
        start = 0
        while True:
            i = buf.find(REC, start)
            if i < 0:
                break
            if HDR.match(buf, i):
                nl = int.from_bytes(buf[i + 12:i + 15], 'big')
                if 0 < nl < 4096 and i + 15 + nl <= len(buf):
                    raw = buf[i + 15:i + 15 + nl]
                    if all(32 <= b < 127 for b in raw):
                        payload = i + 15 + nl
                        recs[base + payload] = (base + i, base + payload,
                                                raw.decode('latin-1'))
            start = i + 5
        # length-framed records: [00 <u8 len> | <u16 BE len>] <path> <payload>
        for p in range(1, len(buf) - 1):
            if buf[p - 1] == 0:
                ln = buf[p]
                if 8 <= ln <= 255:
                    if p + ln >= len(buf) - 4:
                        continue  # near the end: re-examine next chunk
                    if base + p - 1 in seen:
                        continue
                    raw = buf[p + 1:p + 1 + ln]
                    if raw[:1].isalpha() or raw[:1] == b'/':
                        if all(32 <= b < 127 for b in raw) and b'/' in raw:
                            seen.add(base + p - 1)
                            recs[base + p + 1 + ln] = (base + p - 1,
                                                      base + p + 1 + ln,
                                                      raw.decode('latin-1'))
        # u16-framed records anchored by the compress magic
        start = 0
        while True:
            i = buf.find(MAGIC, start)
            if i < 0:
                break
            start = i + 2
            if i >= len(buf) - CONTEXT:
                break  # near the chunk end: re-examine next chunk
            if base + i in seen:
                continue
            for p in range(max(0, i - MAXPATH), i):
                if p < 2:
                    continue
                ln = (buf[p - 2] << 8) | buf[p - 1]
                if ln != i - p or not 8 <= ln <= 512:
                    continue
                path = buf[p:i]
                if not (path[:1].isalpha() or path[:1] == b'/'):
                    continue
                if all(32 <= b < 127 for b in path):
                    seen.add(base + i)
                    recs[base + i] = (base + p - 2, base + i,
                                      path.decode('latin-1'))
                    break

    while off < total:
        piece = reader.read_logical(e, info, off, min(CHUNK, total - off))
        if not piece:
            break
        buf = tail + piece
        emit(buf, off - len(tail))
        tail = buf[-TAIL:]
        off += len(piece)

    ordered = sorted(recs.items())
    out = []
    for k, (payload, (fstart, pstart, path)) in enumerate(ordered):
        # payload length = next record's framing start - payload start; the
        # trailing record runs to the logical end of the archive
        plen = ordered[k + 1][1][0] - payload if k + 1 < len(ordered) else 0
        out.append((path, plen))
    return out


def main():
    iso_path = sys.argv[1]
    slug = os.path.basename(iso_path).replace('.iso', '').replace(' ', '_')
    e = efs.EFS(iso_path)
    files = e.walk()
    # scan .sw*, *_hdr, .src records (skip books/man/data)
    targets = []
    for p, ft, s, ino in files:
        if ft == 0o100000:
            b = p.rsplit('/', 1)[-1].lower()
            if (b.endswith('.sw') or b.endswith('.sw32') or b.endswith('.sw64')
                    or b.endswith('_hdr') or b.endswith('.hdr') or b.endswith('.src')):
                targets.append((p, s, ino))
    allpaths = []
    perfile = {}
    for p, s, ino in targets:
        info = e.inode(ino)
        ps = _walk_records(e, info)
        if ps:
            perfile[p] = ps
            allpaths.extend(path for path, plen in ps)
    uniq = sorted(set(allpaths))
    out = f'/tmp/sw-paths-{slug}.txt'
    with open(out, 'w') as fh:
        for p in uniq:
            fh.write(p + '\n')
    print(f'=== {os.path.basename(iso_path)}')
    print(f'scanned {len(targets)} archive files ({len(perfile)} contained records); '
          f'{len(allpaths)} member records, {len(uniq)} unique installed paths')
    print(f'path list -> {out}')
    print('per-archive member counts:')
    for p in sorted(perfile):
        print(f'   {len(perfile[p]):>6}  {p}')
    print()

    def show(label, pred, limit=25):
        hits = [p for p in uniq if pred(p)]
        print(f'[{label}] count={len(hits)}')
        for p in hits[:limit]:
            print('   ' + p)

    show('usr/include', lambda p: '/usr/include/' in ('/' + p) or p.startswith('usr/include'))
    show('*.h', lambda p: p.lower().endswith('.h'), 30)
    show('debug paths', lambda p: '/debug/' in ('/' + p))
    show('crt*', lambda p: p.rsplit('/', 1)[-1].startswith(('crt1.o', 'crti.o', 'crtn.o', 'crtbegin', 'crtend')))
    show('libc/libm', lambda p: p.rsplit('/', 1)[-1].startswith(('libc.so', 'libc.a', 'libm.so', 'libm.a')))
    show('compiler bins', lambda p: p.rsplit('/', 1)[-1] in
         ('cfe', 'uopt', 'ugen', 'as1', 'ld', 'cc', 'gcc', 'ido', 'accom', 'copt', 'upas', 'as0', 'cpp'))
    show('mipspro-ish paths', lambda p: any(k in p.lower() for k in
         ('mipspro', 'libkapi', 'libcfe', 'cfe', 'abi/lib')))
    show('unix/sash', lambda p: p.rsplit('/', 1)[-1] in ('unix', 'sash'))
    e.f.close()


if __name__ == '__main__':
    main()
