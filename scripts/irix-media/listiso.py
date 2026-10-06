#!/usr/bin/env python3
"""List EFS paths matching a substring, with file sizes.

Usage: python3 listiso.py <image> [pattern]      (default pattern '.sw')
"""
import sys

import efs

iso = sys.argv[1]
pat = sys.argv[2] if len(sys.argv) > 2 else '.sw'
e = efs.EFS(iso)
for p, ft, s, ino in e.walk():
    if pat in p:
        print(f"{p}  {s}")
