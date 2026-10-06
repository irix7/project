#!/usr/bin/env python3
"""Job-driven extractor for IRIX installer (inst) records inside EFS images.

IRIX "software distribution" payloads (``dist/*.sw`` files on the CDs) are
streams of records: a little-endian u16 name length, the installed path, then
the payload — usually Unix ``compress`` (.Z / LZW) data. A job is

    [image, sw_path, record_path, cmpsize, size, bsdsum, hint_offset]

where ``cmpsize`` is the compressed payload length in bytes (0 means the
record is stored raw), ``size`` and ``bsdsum`` are the expected decompressed
size and BSD-style 16-bit rotating sum (0 disables the check), and
``hint_offset`` is a known near-match byte offset tried before a full stream
scan. Output files are written under the output root mirroring each record
path; extracted content never lands in the repository.

The decoder is the pure-python LZW of ``lzw.py`` with a guard on the
prefix-chain walk: some dev-CD streams make the plain decoder diverge, and
this raises ``BADZ`` instead of hanging. For those streams, decompress the
payload with the system ``gzip -dc`` instead.

Usage: python3 extract-sw.py jobs.json <outroot>
"""
import json
import os
import sys

import efs
import reader

DEFAULT_CMP = 4 << 20


def bsdsum(data):
    s = 0
    for b in data:
        s = ((s >> 1) | ((s & 1) << 15))
        s = (s + b) & 0xFFFF
    return s


def unlzw_checked(data):
    """``lzw.unlzw`` with a guard on the prefix-chain walk.

    Some IRIX dev-CD streams make the plain decoder follow a corrupt chain
    forever; this raises instead of hanging. Decoding of well-formed streams
    is byte-identical to ``lzw.unlzw``.
    """
    if len(data) < 3 or data[0] != 0x1F or data[1] != 0x9D:
        raise ValueError('not compress data')
    maxbits = data[2] & 0x1F
    block_mode = bool(data[2] & 0x80)
    maxmaxcode = 1 << maxbits
    n_bits = 9
    maxcode = (1 << n_bits) - 1
    free_ent = 257 if block_mode else 256
    prefix = [0] * maxmaxcode
    suffix = [0] * maxmaxcode
    out = bytearray()
    pos = 3
    bitbuf = 0
    bitcnt = 0

    def getcode():
        nonlocal pos, bitbuf, bitcnt
        while bitcnt < n_bits:
            if pos >= len(data):
                return -1
            bitbuf |= data[pos] << bitcnt
            pos += 1
            bitcnt += 8
        code = bitbuf & ((1 << n_bits) - 1)
        bitbuf >>= n_bits
        bitcnt -= n_bits
        return code

    code = getcode()
    if code < 0:
        return b''
    finchar = code
    out.append(finchar & 0xFF)
    oldcode = code
    while True:
        code = getcode()
        if code < 0:
            break
        if code == 256 and block_mode:
            free_ent = 257 if block_mode else 256
            n_bits = 9
            maxcode = (1 << n_bits) - 1
            code = getcode()
            if code < 0:
                break
            finchar = code & 0xFF
            out.append(finchar)
            oldcode = code
            continue
        incode = code
        stack = []
        if code >= free_ent:
            stack.append(finchar)
            code = oldcode
        depth = 0
        while code >= 256:
            stack.append(suffix[code])
            code = prefix[code]
            depth += 1
            if depth > maxmaxcode + 10:
                raise ValueError('lzw stream diverges')
        stack.append(code)
        finchar = code & 0xFF
        for b in reversed(stack):
            out.append(b)
        if free_ent < maxmaxcode:
            prefix[free_ent] = oldcode
            suffix[free_ent] = finchar
            free_ent += 1
            if free_ent > maxcode and n_bits < maxbits:
                n_bits += 1
                maxcode = (1 << n_bits) - 1
        oldcode = incode
    return bytes(out)


def locate(efs, info, path, off):
    nb = path.encode()
    if off:
        start = max(0, off - 128)
        buf = reader.read_logical(efs, info, start, 1 << 20)
        idx = buf.find(nb)
        if idx >= 0:
            return start + idx
    return reader.find_record(efs, info, path)


def main():
    jobs = json.load(open(sys.argv[1]))
    outroot = sys.argv[2]
    cache = {}
    for job in jobs:
        iso, sw, path = job[0], job[1], job[2]
        cmpsize = job[3] if len(job) > 3 and job[3] else 0
        size = job[4] if len(job) > 4 else 0
        isum = job[5] if len(job) > 5 else 0
        off = job[6] if len(job) > 6 else 0
        if iso not in cache:
            e = efs.EFS(iso)
            cache[iso] = (e, {p: (ft, s, ino) for p, ft, s, ino in e.walk()})
        e, files = cache[iso]
        if sw not in files:
            print("NOSW", sw)
            continue
        info = e.inode(files[sw][2])
        pos = locate(e, info, path, off)
        if pos is None:
            print("MISS", path)
            continue
        n = cmpsize or size or DEFAULT_CMP
        payload = reader.read_logical(e, info, pos + len(path), n)
        if cmpsize:
            if payload[0:2] != b'\x1f\x9d':
                print("NOTZ", path)
                continue
            try:
                data = unlzw_checked(payload)
            except Exception as ex:
                print("BADZ", path, ex)
                continue
        else:
            data = payload
        rb = bsdsum(data)
        oklen = (not size) or len(data) == size
        oksum = (not isum) or rb == isum
        out = os.path.join(outroot, path.lstrip('/'))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as f:
            f.write(data)
        print(f"{'OK ' if oklen and oksum else 'PART'} {path}  "
              f"len={len(data)}/{size}  sum={isum}  rb={rb}", flush=True)


if __name__ == '__main__':
    main()
