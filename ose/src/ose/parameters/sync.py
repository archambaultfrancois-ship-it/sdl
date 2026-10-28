"""Single-writer parameter tree sync using inotify and OSE subscriptions."""
import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import tempfile

from ose.parameters.files import MAX_JSON_SIZE, read_value, validate_import
from ose.parameters.sync_fs import DirectoryEvents
from ose.transport.tcp import RemoteError

_MISSING = object()
_PATH = re.compile(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z')


@dataclass
class Entry:
    block: object
    file: Path
    mtime: object = None
    value: object = _MISSING
    blocked: bool = False
    error: str = ''


class DirectorySync:
    def __init__(self, directory, connect, *, pattern=None, exact=None, initial='files',
                 debounce=.15, max_json_size=MAX_JSON_SIZE, retry_delay=2,
                 report=None, watcher_factory=DirectoryEvents):
        if initial not in ('files', 'app'):
            raise ValueError('initial sync policy must be files or app')
        if not math.isfinite(debounce) or debounce < 0 or max_json_size <= 0:
            raise ValueError('invalid sync debounce or JSON size limit')
        self.root = Path(directory).resolve()
        self.connect, self.pattern, self.exact = connect, pattern or re.compile('.*'), exact
        self.initial, self.debounce, self.max_json_size = initial, debounce, max_json_size
        self.retry_delay, self.watcher_factory = retry_delay, watcher_factory
        self.report = report or (lambda message: print(f'parameter-sync: {message}', file=sys.stderr, flush=True))
        self.entries, self.pending, self.remote, self.unknown = {}, {}, {}, set()
        self.changed = asyncio.Event()
        self.watcher = None
        self.failure = None

    def safe_file(self, path):
        if not isinstance(path, str) or len(path.encode()) > 200 or not _PATH.fullmatch(path):
            raise ValueError('invalid parameter path')
        file = self.root / (path + '.json')
        for parent in (file, *file.parents):
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError('symlinks are not supported in the parameter tree')
        return file

    def schedule(self, path, delay=None):
        self.pending[path] = asyncio.get_running_loop().time() + (self.debounce if delay is None else delay)
        self.changed.set()

    def filesystem_event(self, file):
        if isinstance(file, BaseException):
            self.failure = file
        elif file is None:
            self.schedule(None)
        elif file.suffix == '.json':
            path = file.relative_to(self.root).as_posix()[:-5]
            if path in self.entries:
                self.schedule(path)
            else:
                self.warn_unknown(path)
        self.changed.set()

    def warn_unknown(self, path):
        selected = path == self.exact if self.exact is not None else bool(self.pattern.search(path))
        if selected and path not in self.entries and path not in self.unknown:
            self.unknown.add(path)
            self.report(f'{path}: unknown parameter file; ignored')

    def scan(self):
        for file in self.watcher.scan():
            self.filesystem_event(file)
        for path in self.entries:
            self.schedule(path)

    def diagnostic(self, path, error):
        entry = self.entries[path]
        # Remote messages/codec errors can contain user values. Logs name the
        # category and path only; CLI one-shot commands retain detailed errors.
        if isinstance(error, RemoteError):
            message = 'service rejected operation'
        elif isinstance(error, ValueError):
            message = 'invalid JSON, SDL value or file layout'
        else:
            message = type(error).__name__
        if entry.error != message:
            entry.error = message
            self.report(f'{path}: {message}; file retained')

    def write(self, path, value):
        entry = self.entries[path]
        file = self.safe_file(path)
        data = (json.dumps({path: value}, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if len(data) > self.max_json_size:
            raise ValueError('export exceeds maximum JSON size')
        file.parent.mkdir(parents=True, exist_ok=True)
        mode = file.stat().st_mode & 0o777 if file.exists() else 0o600
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=file.parent, suffix='.tmp', delete=False) as stream:
                name = stream.name
                os.chmod(name, mode)
                stream.write(data)
            os.replace(name, file)
        finally:
            if name is not None and os.path.exists(name):
                os.unlink(name)
        # Ensure directories created by this write are watched before returning.
        self.watcher.ensure(file.parent)
        entry.mtime, entry.value, entry.blocked, entry.error = file.stat().st_mtime_ns, value, False, ''
        self.remote.pop(path, None)
        self.report(f'{path}: app -> file')

    async def local(self, path, *, force=False):
        entry = self.entries[path]
        try:
            file = self.safe_file(path)
            if not file.exists():
                self.write(path, await entry.block.get())
                return
            if not file.is_file():
                raise ValueError('parameter path is not a regular file')
            mtime = file.stat().st_mtime_ns
            if not force and mtime == entry.mtime:
                return
            entry.blocked = True
            value = read_value(file, self.max_json_size)
            validate_import(value)
            if list(value) != [path]:
                raise ValueError('unit JSON path must match its filename')
            payload = entry.block.values.encode(value[path])
            normalized = entry.block.values.decode(payload)
            await entry.block.set(value[path])
            entry.mtime, entry.value, entry.blocked, entry.error = mtime, normalized, False, ''
            self.remote.pop(path, None)
            self.report(f'{path}: file -> app')
        except RemoteError as error:
            self.diagnostic(path, error)
        except (ConnectionError, asyncio.TimeoutError, EOFError):
            raise
        except OSError as error:
            self.diagnostic(path, error)
            self.schedule(path, self.retry_delay)
        except (ValueError, TypeError) as error:
            entry.blocked = True
            self.diagnostic(path, error)

    async def apply_remote(self, path, value):
        entry = self.entries[path]
        if entry.blocked or path in self.pending:
            return  # Local edit has priority; subscriptions coalesce latest values.
        if value == entry.value:
            return
        try:
            self.write(path, value)
        except (OSError, ValueError) as error:
            self.diagnostic(path, error)
            self.remote[path] = value
            self.schedule(path, self.retry_delay)

    async def watch(self, client):
        try:
            while True:
                update = await client.next()
                if update is not None:
                    path, value = update
                    self.remote[path] = value
                    self.changed.set()
        except Exception as error:
            self.failure = error
            self.changed.set()

    async def session(self):
        self.entries, self.pending, self.remote, self.unknown = {}, {}, {}, set()
        self.failure = None
        tasks = []
        async with AsyncExitStack() as stack:
            client = await stack.enter_async_context(await self.connect())
            paths = [path for path in await client.list()
                     if (path == self.exact if self.exact is not None else self.pattern.search(path))]
            if not paths:
                raise ValueError('parameter sync selection is empty')
            for path in sorted(paths):
                self.entries[path] = Entry(await client.block(path), self.safe_file(path))
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                self.watcher = self.watcher_factory(self.root, self.filesystem_event)
            except OSError as error:
                raise ValueError(f'cannot initialize inotify: {error}') from error
            stack.callback(self.watcher.close)
            for path, entry in self.entries.items():
                if self.initial == 'files':
                    await self.local(path, force=True)
                else:
                    try:
                        # Existing file content is not an import in app mode,
                        # even if its initial replacement needs a disk retry.
                        if entry.file.exists():
                            entry.mtime = entry.file.stat().st_mtime_ns
                        value = await entry.block.get()
                    except RemoteError as error:
                        self.diagnostic(path, error)
                        continue
                    try:
                        self.write(path, value)
                    except (OSError, ValueError) as error:
                        self.diagnostic(path, error)
                        self.remote[path] = value
                        self.schedule(path, self.retry_delay)
            self.scan()
            try:
                paths = list(self.entries)
                for start in range(0, len(paths), 256):
                    watcher = await stack.enter_async_context(await self.connect())
                    group = paths[start:start + 256]
                    # Reuse prepared codecs rather than describe every block twice.
                    watcher.blocks.update({path: self.entries[path].block for path in group})
                    await watcher.subscribe(group)
                    tasks.append(asyncio.create_task(self.watch(watcher)))
                self.report(f'watching {len(paths)} parameters in {self.root}')
                while True:
                    self.changed.clear()
                    if self.failure is not None:
                        raise self.failure
                    now = asyncio.get_running_loop().time()
                    ready = [path for path, deadline in self.pending.items() if deadline <= now]
                    for path in ready:
                        self.pending.pop(path, None)
                        if path is None:
                            self.scan()
                        else:
                            await self.local(path)
                    for path, value in list(self.remote.items()):
                        if path not in self.pending and not self.entries[path].blocked:
                            self.remote.pop(path, None)
                            await self.apply_remote(path, value)
                    if self.changed.is_set():
                        continue
                    timeout = max(0, min(self.pending.values()) - asyncio.get_running_loop().time()) if self.pending else None
                    try:
                        await asyncio.wait_for(self.changed.wait(), timeout)
                    except asyncio.TimeoutError:
                        pass
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        installed = []
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, task.cancel)
                installed.append(sig)
            while True:
                try:
                    await self.session()
                except (ConnectionError, OSError, asyncio.TimeoutError, EOFError):
                    self.report('disconnected; reconnecting')
                    await asyncio.sleep(self.retry_delay)
        except asyncio.CancelledError:
            pass
        finally:
            for sig in installed:
                loop.remove_signal_handler(sig)
