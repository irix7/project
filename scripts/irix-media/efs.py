#!/usr/bin/env python3
"""Read-only parser for SGI EFS filesystem images (IRIX CDs and disk images).

Pure Python, no third-party packages. Parses the SGI disk volume header
(partition table), the EFS superblock, the inode table, extents (direct and
indirect) and directories. ``walk()`` lists the whole image; ``inode()``,
``all_extents()``, ``map_block()``, ``readfile()`` and ``readdir()`` expose
the geometry and content. The class only ever reads the image.

Format facts observed on the IRIX 6.5 CD set:

  * volume header magic 0x0be5a941, partition table at byte 312;
  * EFS partitions carry type 0x05 in the volume-header partition table;
  * the EFS superblock sits one block after the partition start, magic
    0x00072959; the inode table follows, four 128-byte inodes per block,
    striped over cylinder groups;
  * inode extents are ``{magic, block(24-bit), length(24-bit)}`` relative to
    the partition; large files hold their extent table in container blocks
    referenced by the inode's first few extents;
  * directory blocks begin with magic 0xbeef and a slot-offset table.
"""
import os
import struct

BS = 512
S_IFMT = 0o170000
S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000


class EFS:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.size = os.path.getsize(path)
        self._icache = {}
        self._bcount = 0
        self._read_vh()
        self._find_fs()
        self._read_sb()

    def pread(self, off, n):
        self._bcount += 1
        return os.pread(self.f.fileno(), n, off)

    def rd(self, lba, n=BS):
        return self.pread(lba * BS, n)

    def _read_vh(self):
        vh = self.rd(0)
        self.vh = vh
        self.vh_magic = struct.unpack('>I', vh[0:4])[0]
        self.vh_rootpt, self.vh_swappt = struct.unpack('>HH', vh[4:8])
        self.vh_bootfile = vh[8:24].rstrip(b'\x00').decode('latin-1')
        self.vd = []
        for i in range(15):
            o = 72 + i * 16
            name = vh[o:o + 8].rstrip(b'\x00')
            if name:
                lbn, nb = struct.unpack('>II', vh[o + 8:o + 16])
                self.vd.append((name.decode('latin-1'), lbn, nb))
        self.pt = []
        for i in range(16):
            o = 312 + i * 12
            nb, fl, tp = struct.unpack('>III', vh[o:o + 12])
            if nb or fl or tp:
                self.pt.append((i, nb, fl, tp))

    def _find_fs(self):
        self.fs_start = None
        for i, nb, fl, tp in self.pt:
            if tp in (5, 7):
                self.fs_start = fl
        if self.fs_start is None:
            raise SystemExit('no EFS partition')

    def _read_sb(self):
        sb = self.rd(self.fs_start + 1)
        (self.fs_size, self.fs_firstcg, self.fs_cgfsize) = struct.unpack('>III', sb[0:12])
        self.fs_cgisize, self.fs_sectors, self.fs_heads, self.fs_ncg = struct.unpack('>HHHH', sb[12:20])
        self.fs_magic = struct.unpack('>I', sb[28:32])[0]
        self.fs_fname = sb[32:38].rstrip(b'\x00').decode('latin-1')
        self.fs_fpack = sb[38:44].rstrip(b'\x00').decode('latin-1')
        self.fs_tfree, self.fs_tinode = struct.unpack('>II', sb[48:56])
        if self.fs_magic != 0x00072959:
            raise SystemExit('bad EFS magic 0x%08x' % self.fs_magic)

    def inode(self, ino):
        if ino in self._icache:
            return self._icache[ino]
        idx = ino // 4
        block = (self.fs_start + self.fs_firstcg
                 + self.fs_cgfsize * (idx // self.fs_cgisize)
                 + (idx % self.fs_cgisize))
        data = self.rd(block)
        off = (ino % 4) * 128
        d = data[off:off + 128]
        mode, nlink, uid, gid = struct.unpack('>HHHH', d[0:8])
        size, atime, mtime, ctime, gen = struct.unpack('>IIIII', d[8:28])
        numext = struct.unpack('>H', d[28:30])[0]
        exts = []
        for i in range(12):
            raw = d[32 + i * 8:40 + i * 8]
            exts.append((raw[0],
                         (raw[1] << 16) | (raw[2] << 8) | raw[3],
                         raw[4],
                         (raw[5] << 16) | (raw[6] << 8) | raw[7]))
        info = dict(ino=ino, mode=mode, nlink=nlink, uid=uid, gid=gid,
                    size=size, mtime=mtime, numext=numext, exts=exts)
        self._icache[ino] = info
        return info

    def all_extents(self, info):
        n = info['numext']
        exts = info['exts']
        if n <= 12:
            return exts[:n]
        # large-file layout: the inode's first `direxts` extents are container
        # blocks holding an extent table with `n` entries (all data extents)
        direxts = exts[0][3]
        containers = exts[:direxts]
        out = []
        for cur in range(n):
            base = 0
            for de in containers:
                cap = de[2] * (BS // 8)
                if cur < base + cap:
                    j = cur - base
                    blk = self.rd(self.fs_start + de[1] + j // (BS // 8))
                    raw = blk[(j % (BS // 8)) * 8:(j % (BS // 8)) * 8 + 8]
                    out.append((raw[0],
                                (raw[1] << 16) | (raw[2] << 8) | raw[3],
                                raw[4],
                                (raw[5] << 16) | (raw[6] << 8) | raw[7]))
                    break
                base += cap
            else:
                break
        return out

    def map_block(self, info, logical):
        for m, bn, ln, of in self.all_extents(info):
            if of <= logical < of + ln:
                return self.fs_start + bn + (logical - of)
        return None

    def readfile(self, info):
        data = bytearray()
        nb = (info['size'] + BS - 1) // BS
        for b in range(nb):
            pb = self.map_block(info, b)
            if pb is None:
                break
            data += self.rd(pb)
        return bytes(data[:info['size']])

    def readdir(self, info):
        entries = []
        nb = (info['size'] + BS - 1) // BS
        for b in range(nb):
            pb = self.map_block(info, b)
            if pb is None:
                break
            d = self.rd(pb)
            magic, firstused, slots = struct.unpack('>HBB', d[:4])
            if magic != 0xbeef:
                continue
            for s in range(slots):
                so = d[4 + s]
                if so == 0:
                    continue
                real = so << 1
                if real + 5 > BS:
                    continue
                ino = struct.unpack('>I', d[real:real + 4])[0]
                nl = d[real + 4]
                name = d[real + 5:real + 5 + nl].decode('latin-1')
                if name in ('.', '..') or not name:
                    continue
                entries.append((name, ino))
        return entries

    def walk(self):
        entries = []
        visited = set()
        stack = [('', 2)]
        while stack:
            path, ino = stack.pop()
            if ino in visited:
                continue
            visited.add(ino)
            info = self.inode(ino)
            if (info['mode'] & S_IFMT) != S_IFDIR:
                continue
            for name, cino in self.readdir(info):
                ci = self.inode(cino)
                full = path + '/' + name
                ft = ci['mode'] & S_IFMT
                isdir = ft == S_IFDIR
                entries.append((full, ft, ci['size'], cino))
                if isdir:
                    stack.append((full, cino))
        return entries
