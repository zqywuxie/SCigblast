"""ZIP downloads with bounded buffers; analysis artifacts are read unchanged."""
import zipfile


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
    sink = _ZipSink()
    with zipfile.ZipFile(sink, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path, name in files:
            # Preserve the source basename and relative directory inside the ZIP.
            with path.open('rb') as source, archive.open(name, 'w', force_zip64=True) as target:
                yield from sink.drain()
                while chunk := source.read(chunk_size):
                    target.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()
