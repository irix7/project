# IRIX media tooling

Read-only tooling for the SGI IRIX 6.5 CD images: the EFS filesystem the CDs
use and the IRIX "software distribution" (`inst`) record streams inside them.
Pure Python 3, no third-party packages, and every script only reads its input
image.

## Modules

| File | Role |
| --- | --- |
| `efs.py` | `EFS` class: volume header, superblock, inodes, extents and directories. `walk()` lists the whole image; `inode()`, `all_extents()`, `map_block()`, `readfile()` and `readdir()` expose the geometry. |
| `lzw.py` | `unlzw()` decompresses Unix `compress` (`.Z`) streams — the format IRIX `inst` uses for payloads — and `records()` finds the `im001` record headers in a buffer. |
| `reader.py` | `read_logical()` reads a byte range of an inode's logical stream across its extents without loading the file; `find_record()` stream-scans a large archive for a record path in 4 MiB windows. |
| `listiso.py` | Driver: print every EFS path containing a substring, with sizes. |
| `readiso.py` | Driver: extract one file by exact EFS path. |
| `swscan.py` | Driver: list the installed paths carried by every `dist/*.sw` archive in an image, without decompressing. |
| `extract-sw.py` | Driver: job-driven extraction of `inst` records, decompressing and checksumming each payload (see `jobs.example.json`). |

The drivers import their sibling modules directly from this directory, so
they run as plain scripts from anywhere:

```sh
# Every EFS path mentioning debug on the Development Libraries CD:
python3 scripts/irix-media/listiso.py "/path/to/IRIX 6.5 Development Libraries June 1998.iso" debug

# One file by exact path:
python3 scripts/irix-media/readiso.py "/path/to/IRIX 6.5 Foundation 1.iso" /dist/README /tmp/README

# All installed paths inside every .sw archive:
python3 scripts/irix-media/swscan.py "/path/to/IRIX 6.5 Foundation 1.iso"

# Job-driven extraction (decompresses and verifies each record):
python3 scripts/irix-media/extract-sw.py jobs.json /path/to/outroot
```

A job for `extract-sw.py` is `[image, sw_path, record_path, cmpsize, size,
bsdsum, hint_offset]`: `cmpsize` is the compressed payload length in bytes
(0 means the record is stored raw), `size` and `bsdsum` are the expected
decompressed size and BSD-style 16-bit rotating sum (0 disables the check),
and `hint_offset` is a known near-match byte offset tried before a full
stream scan. `jobs.example.json` shows the shape; the tool writes files
under the output root mirroring each record path.

## Format facts

- The CDs carry an SGI **disk volume header** (dvh) in block 0: magic
  `0x0be5a941`, volume-header files from byte 72, a 16-slot partition table
  from byte 312. EFS partitions are type `0x05`.
- The **EFS superblock** sits one block after the partition start; magic
  `0x00072959` on the observed 6.5 images. The inode table follows, four
  128-byte inodes per block, striped over cylinder groups.
- Inodes carry up to 12 direct extents
  (`{magic, block(24-bit), length(24-bit)}` relative to the partition);
  larger files hold their extent table in container blocks referenced by the
  inode's first few extents.
- Directory blocks begin with magic `0xbeef` and a table of slot offsets.
- The discs are EFS-only: none of the observed IRIX 6.5 CD images carries an
  ISO9660 layer.
- Installer payloads (`dist/*.sw`, `*.src`, `*_hdr`) are record streams. The
  first record starts with an `im001` header (a version field and a big-endian
  length); every record is `[0x00 <u8 length> | <u16 BE length>] <installed
  path> <payload>`, and the payload is usually Unix `compress` (`.Z`) LZW
  data with magic `0x1f9d`, or raw content for small text files.
- `lzw.unlzw()` decodes LSB-first as ncompress writes it. The plain decoder
  diverges on some dev-CD streams (a known limit); `extract-sw.py` raises
  instead of hanging, and the system `gzip -dc` decodes those streams.

## Licence and provenance

These scripts are authored by this project: no SGI source, no third-party
code, and nothing extracted from the media is published. The format facts
above come from public documentation and read-only observation of
project-owned media. This stays inside the ADR-0001 boundary
(`docs/publication.md`): tooling and documentation only.
