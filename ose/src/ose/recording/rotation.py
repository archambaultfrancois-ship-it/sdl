"""Recording rotation settings and exclusive ownership of numbered output files."""
from collections import deque
from decimal import Decimal, localcontext
import errno
import os
from pathlib import Path
import re
import stat


def positive_setting(value, name):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive integer or off")
    return value


def parse_size(value):
    if value.lower() == "off":
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(B|KiB|MiB|GiB)?", value, re.IGNORECASE)
    if not match:
        raise ValueError("max-size expects a positive size in B, KiB, MiB or GiB, or off")
    scale = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3}[(match[2] or "B").lower()]
    with localcontext() as context:
        context.prec = max(28, len(match[1]) + 12)
        size = Decimal(match[1]) * scale
    if size < 1 or size != size.to_integral_value():
        raise ValueError("max-size must resolve to a positive whole number of bytes")
    return int(size)


class OutputFiles:
    def __init__(self, path, *, numbered=False, cycle=None):
        requested = Path(path).absolute()
        self.base = requested.parent.resolve() / requested.name
        self.numbered, self.cycle = numbered, cycle
        self.index = 0
        self.path = self.name(1)
        self.stream = None
        self.retained = deque()

    def name(self, index):
        if not self.numbered:
            return self.base
        return self.base.with_name(f"{self.base.stem}_{index:03d}{self.base.suffix}")

    def preflight(self):
        if self.base.exists() or self.base.is_symlink():
            raise FileExistsError(self.base)
        if self.numbered:
            pattern = re.compile(re.escape(self.base.stem) + r"_\d{3,}" + re.escape(self.base.suffix))
            for path in self.base.parent.iterdir():
                if pattern.fullmatch(path.name):
                    raise FileExistsError(path)

    @staticmethod
    def identity(info):
        return info.st_dev, info.st_ino

    def check_owned(self, path, identity):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or self.identity(info) != identity:
            raise OSError(f"recording output was replaced outside this capture: {path}")

    def check_next(self):
        path = self.name(self.index + 1)
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
        if self.cycle is not None and len(self.retained) == self.cycle:
            self.check_owned(*self.retained[0])

    def open_next(self):
        if self.stream is not None and not self.stream.closed:
            raise RuntimeError("close the current recording before rotating")
        self.check_next()
        path = self.name(self.index + 1)
        if self.cycle is not None and len(self.retained) == self.cycle:
            old, identity = self.retained[0]
            self.check_owned(old, identity)
            stream = old.open("r+b")
            linked = False
            try:
                if self.identity(os.fstat(stream.fileno())) != identity:
                    raise OSError(f"recording output changed while reopening: {old}")
                try:
                    # A no-clobber rename: retain the inode, claim the new name
                    # exclusively, then remove its old name and truncate it.
                    link = getattr(os, "link", None)
                    if link is None:
                        raise OSError(errno.EOPNOTSUPP, "hard links are unavailable")
                    link(old, path, follow_symlinks=False)
                    linked = True
                except OSError as error:
                    if error.errno not in (errno.EPERM, errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS):
                        raise
                    # Some external/mobile filesystems cannot make hard links.
                    replacement = path.open("xb")
                    stream.close()
                    stream = replacement
                self.check_owned(old, identity)
                old.unlink()
                stream.seek(0)
                stream.truncate()
            except BaseException:
                stream.close()
                # Only undo the hard link this operation itself created.
                if linked and path.exists() and self.identity(path.lstat()) == identity:
                    path.unlink()
                raise
            self.retained.popleft()
        else:
            stream = path.open("xb")
        self.stream, self.path = stream, path
        self.index += 1
        if self.cycle is not None:
            self.retained.append((path, self.identity(os.fstat(stream.fileno()))))
        return stream

    def close(self):
        if self.stream is not None:
            self.stream.close()

    def status(self):
        return {"file_index": self.index, "rotations": max(0, self.index - 1),
                "file_count": min(self.index, self.cycle) if self.cycle is not None else self.index}
