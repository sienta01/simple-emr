"""Minimal reader for dBASE / Visual FoxPro tables (.dbf), enough for importing old clinic software."""
import struct
from pathlib import Path

# Language driver byte in the header -> Python codec. Unknown drivers fall back to Windows ANSI.
CODEPAGES = {0x01: 'cp437', 0x02: 'cp850', 0x03: 'cp1252', 0x57: 'cp1252', 0x64: 'cp852', 0xC8: 'cp1250'}


def read_dbf(path, fields=None, encoding=None):
    """Yields each live (not deleted) record as {FIELD: stripped text}.

    Every value is returned as text exactly as stored (dates as YYYYMMDD, logicals as T/F); the caller
    converts. `fields` limits decoding to those columns, which keeps memory low on large tables.
    """
    with Path(path).open('rb') as fh:
        header = fh.read(32)
        if len(header) < 32:
            raise ValueError(f'{path}: not a DBF file')
        count, header_len, record_len = struct.unpack('<IHH', header[4:12])
        encoding = encoding or CODEPAGES.get(header[29], 'cp1252')

        columns = []
        offset = 1  # byte 0 of each record is the deletion flag
        while True:
            desc = fh.read(32)
            if len(desc) < 32 or desc[0] == 0x0D:
                break
            name = desc[:11].split(b'\0')[0].decode('ascii', 'replace').upper()
            length = desc[16]
            if desc[11:12] == b'C':
                length += desc[17] << 8  # Clipper stores long text columns' high byte in "decimals"
            if fields is None or name in fields:
                columns.append((name, offset, length))
            offset += length
        if fields is not None:
            missing = set(fields) - {name for name, _, _ in columns}
            if missing:
                raise ValueError(f'{path}: missing columns {", ".join(sorted(missing))}')

        fh.seek(header_len)
        for _ in range(count):
            record = fh.read(record_len)
            if len(record) < record_len or record[:1] == b'\x1a':
                break
            if record[:1] == b'*':
                continue
            yield {
                name: record[offset:offset + length].decode(encoding, 'replace').strip()
                for name, offset, length in columns
            }
