#!/usr/bin/env python3
"""Extract one file from an EFS image by exact path.

Usage: python3 readiso.py <image> <path> <out-file>
"""
import sys

import efs
import reader

iso, path, out = sys.argv[1], sys.argv[2], sys.argv[3]
e = efs.EFS(iso)
for p, ft, s, ino in e.walk():
    if p == path:
        info = e.inode(ino)
        data = reader.read_logical(e, info, 0, info['size'])
        with open(out, 'wb') as f:
            f.write(data)
        print("wrote", out, len(data))
        break
else:
    print("MISS", path)
