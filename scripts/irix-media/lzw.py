#!/usr/bin/env python3
"""Pure-python Unix compress (LZW, .Z) decompressor + IRIX .sw record extraction.

Bit packing per ncompress compress.c: codes are written little-endian within
the byte stream — output(b,o,c,n) { p[o>>3] |= c << (o&7); o += n; } —
so the decoder reads a code as (accumulator >> 0) & mask, LSB-first.
"""


def unlzw(data):
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

    def reset_table():
        nonlocal n_bits, maxcode, free_ent
        free_ent = 257 if block_mode else 256
        n_bits = 9
        maxcode = (1 << n_bits) - 1

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
            reset_table()
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
        while code >= 256:
            stack.append(suffix[code])
            code = prefix[code]
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


def records(buf):
    """Yield (path, start_of_payload, end) for each im001 record in buf."""
    import re
    hdr = re.compile(rb'im001V\d{3}P\d{2}')
    out = []
    for mt in hdr.finditer(buf):
        i = mt.start()
        if i + 15 > len(buf):
            continue
        nl = int.from_bytes(buf[i + 12:i + 15], 'big')
        if 0 < nl < 4096 and i + 15 + nl <= len(buf):
            raw = buf[i + 15:i + 15 + nl]
            if all(32 <= b < 127 for b in raw):
                out.append((raw.decode('latin-1'), i + 15 + nl))
    res = []
    for j, (path, payload_start) in enumerate(out):
        end = out[j + 1][1] if j + 1 < len(out) else len(buf)
        res.append((path, payload_start, end))
    return res
