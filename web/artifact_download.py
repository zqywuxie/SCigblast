"""ZIP downloads with bounded buffers; analysis artifacts are read unchanged."""
import zipfile
from pathlib import Path


def file_response(path, *, download=True, filename=None):
    """Explicit export types; Linux MIME registries may not know XLSX."""
    from fastapi.responses import FileResponse
    path = Path(path)
    media_type = {
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.xls': 'application/vnd.ms-excel', '.png': 'image/png',
        '.zip': 'application/zip', '.gz': 'application/gzip',
        '.csv': 'text/csv', '.tsv': 'text/tab-separated-values',
        '.json': 'application/json', '.log': 'text/plain', '.txt': 'text/plain',
        '.html': 'text/html',
        '.fasta': 'text/plain', '.fa': 'text/plain', '.fq': 'text/plain', '.fastq': 'text/plain',
    }.get(path.suffix.lower(), 'application/octet-stream')
    return FileResponse(path, media_type=media_type,
                        filename=(filename or path.name) if download else None)


class _ZipSink:
    def __init__(self):
        self.position = 0
        self.chunks = []

    def write(self, data):
        self.position += len(data)
        if data:
            self.chunks.append(data)
        return len(data)

    def tell(self):
        return self.position

    def flush(self):
        pass

    def drain(self):
        chunks, self.chunks = self.chunks, []
        return chunks


def stream_zip(files, chunk_size=128 * 1024):
    """Yield a standards-compliant ZIP without a full archive in RAM or on disk."""
    def chunks(path):
        with path.open('rb') as source:
            while chunk:=source.read(chunk_size):yield chunk
    yield from stream_zip_members((name,chunks(path)) for path,name in files)


def stream_zip_members(members):
    """Members may be streamed HTML or file chunks; no temporary ZIP needed."""
    sink = _ZipSink()
    with zipfile.ZipFile(sink, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name,chunks in members:
            with archive.open(name, 'w', force_zip64=True) as target:
                yield from sink.drain()
                for chunk in chunks:
                    target.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()
