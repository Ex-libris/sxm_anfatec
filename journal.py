"""Dedicated JSONL journal, independent of application logging configuration."""
import json
import os
from pathlib import Path


class EventJournal:
    def __init__(self, path, max_bytes=10_000_000, backups=5):
        self.path, self.max_bytes, self.backups = Path(path), max_bytes, backups
        self.fh = self.path.open('ab')
        self.written = 0

    def append(self, event):
        from .recorder import clean
        data = (json.dumps(clean(event), ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        if self.fh.tell() and self.fh.tell() + len(data) > self.max_bytes:
            self.sync()
            self.fh.close()
            for n in range(self.backups, 0, -1):
                source = self.path if n == 1 else self.path.with_name(self.path.name + f'.{n-1}')
                target = self.path.with_name(self.path.name + f'.{n}')
                if source.exists():
                    os.replace(source, target)
            self.fh = self.path.open('ab')
        self.fh.write(data)
        self.fh.flush()
        self.written += 1

    def sync(self):
        self.fh.flush()
        os.fsync(self.fh.fileno())

    def close(self):
        try:
            self.sync()
        finally:
            self.fh.close()
