import asyncio
import hashlib
import io
import posixpath
import queue
import re
import shlex
import shutil
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import IO, Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

from rqark_sandbox.config import WORKSPACE_MAX_BYTES, WORKSPACE_SHELL_MAX_BYTES


class WorkspaceError(Exception):
    pass


class PathOutsideWorkspaceError(WorkspaceError):
    pass


class FileMissingError(WorkspaceError):
    pass


class IsDirectoryError(WorkspaceError):
    pass


class PathNotDirectoryError(WorkspaceError):
    pass


class FileTooLargeError(WorkspaceError):
    pass


class ConflictError(WorkspaceError):
    pass


class PreconditionFailedError(WorkspaceError):
    pass


class WorkspaceNotReadyError(WorkspaceError):
    pass


class InvalidOperationError(WorkspaceError):
    pass


@dataclass(frozen=True)
class ShellResult:
    exit_code: int
    stdout: str
    stderr: str


def _text(data: bytes | None) -> str:
    return data.decode('utf-8', errors='replace') if data else ''


def _clean_subdir(subdir: str | None) -> str:
    """Normalises a project path into a form safe to join onto WORKDIR.

    A project's path arrives from data cloud, where it was validated on write.
    It is checked again here because this is the layer that turns it into a
    filesystem path: an absolute path, a ".." segment or a NUL would otherwise
    let a project reach outside the volume.
    """
    raw = (subdir or '').strip()
    if not raw:
        return ''
    if '\x00' in raw:
        raise PathOutsideWorkspaceError('project path contains a null byte')
    if raw.startswith('/'):
        raise PathOutsideWorkspaceError('project path must be relative')
    parts = [part for part in raw.split('/') if part not in ('', '.')]
    if '..' in parts:
        raise PathOutsideWorkspaceError('project path must not contain ".."')
    if not parts:
        return ''
    return '/'.join(parts)


class _IteratorReader(io.RawIOBase):
    """Adapts docker's chunk iterator into a file object, so tar reads it lazily."""

    def __init__(self, chunks: Iterator[bytes]):
        self._chunks = chunks
        self._pending = b''

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while not self._pending:
            try:
                self._pending = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(buffer), len(self._pending))
        buffer[:size] = self._pending[:size]
        self._pending = self._pending[size:]
        return size


def _close_quietly(stream: Any) -> None:
    close = getattr(stream, 'close', None)
    if close is not None:
        try:
            close()
        except Exception:
            pass


def _open_archive(stream: Any) -> tarfile.TarFile:
    """Streams a docker archive instead of draining it whole into memory first."""
    if hasattr(stream, 'read'):
        return tarfile.open(fileobj=stream, mode='r|')
    return tarfile.open(fileobj=io.BufferedReader(_IteratorReader(iter(stream))), mode='r|')


def _drain_capped(chunks: Iterator[Any], limit: int) -> tuple[bytes, bytes, bool]:
    """Reads a demuxed exec stream, keeping at most `limit` bytes in total.

    The rest is still consumed (and dropped) so docker closes the exec socket
    and the exit code stays inspectable.
    """
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    size = 0
    truncated = False
    for chunk in chunks:
        stdout, stderr = chunk if isinstance(chunk, tuple) else (chunk, None)
        for part, sink in ((stdout, stdout_parts), (stderr, stderr_parts)):
            if not part or truncated:
                continue
            room = limit - size
            if room <= 0:
                truncated = True
                continue
            if len(part) > room:
                part = part[:room]
                truncated = True
            sink.append(part)
            size += len(part)
    return b''.join(stdout_parts), b''.join(stderr_parts), truncated


@dataclass(frozen=True)
class Entry:
    name: str
    path: str
    type: str
    size: int
    mtime: float
    target: str | None = None


_TYPES = {'f': 'file', 'd': 'dir', 'l': 'link'}

SENSITIVE_NAMES = frozenset({'.env', '.git-credentials', '.netrc', '.npmrc', '.pypirc', 'credentials', 'id_ed25519', 'id_rsa'})
SENSITIVE_PREFIXES = ('.env.', 'id_rsa.', 'id_ed25519.')
REDACT_MAX_BYTES = 4 * 1024 * 1024

_CREDENTIAL_URL = re.compile(r'(?P<scheme>https?://)[^/\s@]+:[^/\s@]+@')
_SECRET_LINE = re.compile(r'(?im)^([ \t]*[A-Za-z0-9_.-]*(?:token|secret|password|passwd|api[_-]?key|authorization)[A-Za-z0-9_.-]*[ \t]*[:=][ \t]*)(\S.*)$')


def _safe_name(name: str | None) -> str:
    value = (name or '').strip()
    if not value or value in ('.', '..') or '/' in value or '\x00' in value or len(value) > 255:
        raise PathOutsideWorkspaceError(f'invalid file name: {name!r}')
    return value


def is_sensitive(path: str) -> bool:
    name = posixpath.basename(path.rstrip('/'))
    parts = path.strip('/').split('/')
    if name in SENSITIVE_NAMES or name.startswith(SENSITIVE_PREFIXES):
        return True
    return '.git' in parts and name == 'config'


def redact(data: bytes) -> bytes:
    text = data.decode('utf-8', errors='replace')
    text = _CREDENTIAL_URL.sub(r'\g<scheme>***@', text)
    text = _SECRET_LINE.sub(r'\1***', text)
    return text.encode('utf-8')


class _IteratorStream(io.RawIOBase):
    def __init__(self, chunks: Iterator[bytes]):
        self._chunks = iter(chunks)
        self._buffer = b''

    def readable(self) -> bool:
        return True

    def readinto(self, target) -> int:
        while not self._buffer:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(target), len(self._buffer))
        target[:size] = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return size


def _tar_file_chunks(stream: Iterator[bytes]) -> Iterator[bytes]:
    reader = io.BufferedReader(_IteratorStream(stream))
    with tarfile.open(fileobj=reader, mode='r|') as tar:
        for member in tar:
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            while chunk := handle.read(64 * 1024):
                yield chunk


def _zip_chunks(stream: Iterator[bytes]) -> Iterator[bytes]:
    sink: queue.Queue[bytes | None] = queue.Queue(maxsize=8)
    stop = threading.Event()
    failure: list[BaseException] = []

    class _Sink:
        def write(self, data: bytes) -> int:
            while not stop.is_set():
                try:
                    sink.put(bytes(data), timeout=0.5)
                    return len(data)
                except queue.Full:
                    continue
            raise BrokenPipeError('client disconnected')

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    def produce() -> None:
        try:
            reader = io.BufferedReader(_IteratorStream(stream))
            with zipfile.ZipFile(_Sink(), 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                with tarfile.open(fileobj=reader, mode='r|') as tar:
                    for member in tar:
                        name = member.name.removeprefix('./').rstrip('/')
                        if not name or name == '.':
                            continue
                        if member.isdir():
                            archive.writestr(f'{name}/', b'')
                        elif member.isfile():
                            handle = tar.extractfile(member)
                            if handle is None:
                                continue
                            with archive.open(name, 'w') as target:
                                shutil.copyfileobj(handle, target, 64 * 1024)
        except BaseException as error:
            failure.append(error)
        finally:
            while True:
                try:
                    sink.put(None, timeout=0.5)
                    break
                except queue.Full:
                    if stop.is_set():
                        break

    worker = threading.Thread(target=produce, name='rqark-workspace-zip', daemon=True)
    worker.start()
    try:
        while True:
            chunk = sink.get()
            if chunk is None:
                break
            yield chunk
    finally:
        stop.set()
        while True:
            try:
                sink.get_nowait()
            except queue.Empty:
                break
    if failure:
        raise WorkspaceError(f'cannot build archive: {failure[0]}') from failure[0]


class _SizedReader:
    def __init__(self, source: IO[bytes], size: int):
        self._source = source
        self._remaining = size

    @property
    def remaining(self) -> int:
        return self._remaining

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > self._remaining:
            size = self._remaining
        data = self._source.read(size)
        if data is None:
            data = b''
        self._remaining -= len(data)
        return data


_client: docker.DockerClient | None = None
_client_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _client_instance() -> docker.DockerClient:
    global _client
    with _client_guard:
        if _client is None:
            _client = docker.from_env()
        return _client


def _lock(name: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(name, threading.Lock())


class Workspace:
    WORKDIR: str = '/workspace'
    RESERVED_NETWORKS: frozenset[str] = frozenset({'none', 'host', 'bridge'})
    COMPLETE_MAX_DEPTH: int = 8
    COMPLETE_OUTPUT_BYTES: int = 256 * 1024
    COMPLETE_IGNORED: tuple[str, ...] = ('.git', '.hg', '.svn', '.venv', 'venv', '__pycache__', 'node_modules', 'dist', 'build', '.tox', '.rqark-tmp')

    def __init__(self, user_id: str, image: str = 'ubuntu:24.04', network: str | None = None, memory: str = '1g', nano_cpus: int = 1_000_000_000, pids_limit: int = 256, cap_add: Sequence[str] = (), subdir: str = ''):
        self.user_id = user_id
        self.image = image
        self.network = network
        self.memory = memory
        self.nano_cpus = nano_cpus
        self.pids_limit = pids_limit
        self.cap_add = tuple(cap_add)
        self._subdir = ''
        self._workdir_ready = False
        self.subdir = subdir

    @property
    def subdir(self) -> str:
        """The project's directory relative to WORKDIR, or '' for the root."""
        return self._subdir

    @subdir.setter
    def subdir(self, value: str | None) -> None:
        cleaned = _clean_subdir(value)
        if cleaned != self._subdir:
            self._workdir_ready = False
        self._subdir = cleaned

    @property
    def workdir(self) -> str:
        """The directory relative paths resolve against and the agent works in."""
        return Workspace.WORKDIR if not self._subdir else f'{Workspace.WORKDIR}/{self._subdir}'

    @property
    def name(self) -> str:
        return f'rqark-user-{self.slug}'

    @property
    def container_name(self) -> str:
        return f'{self.name}-workspace'

    @property
    def slug(self) -> str:
        cleaned = ''.join(char if char.isalnum() or char in '-_.' else '-' for char in self.user_id).strip('-_')
        if not cleaned:
            return hashlib.sha256(self.user_id.encode('utf-8')).hexdigest()[:12]
        if cleaned == self.user_id:
            return cleaned
        return f'{cleaned[:40]}-{hashlib.sha256(self.user_id.encode("utf-8")).hexdigest()[:8]}'

    @property
    def network_name(self) -> str:
        return f'{self.name}-net'

    def to_container_path(self, path: str | None, default: str = '.') -> str:
        raw = (path or default).strip() or default
        joined = raw if raw.startswith('/') else posixpath.join(self.workdir, raw)
        normalized = posixpath.normpath(joined)
        if normalized != Workspace.WORKDIR and not normalized.startswith(Workspace.WORKDIR + '/'):
            raise PathOutsideWorkspaceError(f'path outside workspace: {raw}')
        return normalized

    def api_path(self, path: str | None) -> str:
        raw = (path or '').strip()
        if '\x00' in raw:
            raise PathOutsideWorkspaceError('path contains a null byte')
        if raw.startswith('/'):
            raise PathOutsideWorkspaceError('absolute paths are not allowed')
        parts = [part for part in raw.split('/') if part not in ('', '.')]
        if '..' in parts:
            raise PathOutsideWorkspaceError('path traversal is not allowed')
        return posixpath.join(self.workdir, *parts)

    @classmethod
    def _inside(cls, path: str) -> bool:
        return path == cls.WORKDIR or path.startswith(cls.WORKDIR + '/')

    def _relative(self, container_path: str) -> str:
        return '' if container_path == self.workdir else posixpath.relpath(container_path, self.workdir)

    def relative(self, path: str | None) -> str:
        return self._relative(self.api_path(path))

    def resolve(self, path: str | None, *, strict: bool = True) -> str:
        raw = self.api_path(path)
        if raw == self.workdir:
            probe = raw
        else:
            probe = raw if strict else posixpath.dirname(raw)
        result = self._raw(f'realpath -e -- {shlex.quote(probe)}')
        if result.exit_code != 0 or not result.stdout.strip():
            raise FileMissingError(f'path not found: {path or "."}')
        resolved = result.stdout.strip()
        if not self._inside(resolved):
            raise PathOutsideWorkspaceError(f'path escapes workspace: {path}')
        if raw == self.workdir:
            return resolved
        if strict:
            return resolved
        return posixpath.join(resolved, posixpath.basename(raw))

    def _stat(self, container_path: str) -> Entry:
        result = self._raw(f"find {shlex.quote(container_path)} -mindepth 0 -maxdepth 0 -printf '%y\\0%s\\0%T@\\0%l\\0'")
        if result.exit_code != 0:
            raise FileMissingError(f'path not found: {self._relative(container_path) or "."}')
        fields = result.stdout.split('\0')
        if len(fields) < 4:
            raise WorkspaceError('unexpected output from sandbox')
        kind, size, mtime, target = fields[:4]
        name = '' if container_path == self.workdir else posixpath.basename(container_path)
        return Entry(name=name, path=self._relative(container_path), type=_TYPES.get(kind, 'other'), size=int(size or 0), mtime=float(mtime or 0), target=target or None)

    def _optional(self, container_path: str) -> Entry | None:
        try:
            return self._stat(container_path)
        except FileMissingError:
            return None

    def stat(self, path: str | None) -> Entry:
        return self._stat(self.resolve(path))

    def list_dir(self, path: str | None, show_hidden: bool = False) -> list[Entry]:
        container_path = self.resolve(path)
        result = self._raw(f"find {shlex.quote(container_path)} -mindepth 0 -maxdepth 1 -printf '%y\\0%s\\0%T@\\0%l\\0%P\\0'")
        if result.exit_code != 0:
            raise FileMissingError(f'path not found: {path or "."}')
        fields = result.stdout.split('\0')
        if fields and fields[-1] == '':
            fields.pop()
        if len(fields) % 5:
            raise WorkspaceError('unexpected output from sandbox')
        if fields and fields[0] != 'd':
            raise PathNotDirectoryError(f'not a directory: {path or "."}')
        base = self._relative(container_path)
        entries: list[Entry] = []
        for index in range(0, len(fields), 5):
            kind, size, mtime, target, name = fields[index : index + 5]
            if not name:
                continue
            if not show_hidden and name.startswith('.'):
                continue
            entry_path = posixpath.join(base, name) if base else name
            entries.append(Entry(name=name, path=entry_path, type=_TYPES.get(kind, 'other'), size=int(size or 0), mtime=float(mtime or 0), target=target or None))
        entries.sort(key=lambda entry: (entry.type != 'dir', entry.name.lower()))
        return entries

    @staticmethod
    def _glob_escape(text: str) -> str:
        """Escapes find's `-iname` metacharacters so typed text stays literal."""
        return ''.join(f'\\{char}' if char in '*?[]\\' else char for char in text)

    def complete(self, query: str | None, limit: int = 50) -> list[Entry]:
        """Paths whose basename starts with the text after the last `/`.

        Mirrors the terminal client: the part before the last slash picks the
        directory to search, the rest is a case-insensitive basename prefix.
        Missing directories yield no rows rather than an error, because the
        query is still being typed.
        """
        raw = (query or '').strip()
        if raw.startswith('/'):
            raise PathOutsideWorkspaceError('absolute paths are not allowed')
        base, _, needle = raw.rpartition('/')
        try:
            directory = self.resolve(base)
        except FileMissingError:
            return []
        ignores = ' -o '.join(f'-name {shlex.quote(name)}' for name in self.COMPLETE_IGNORED)
        pattern = f'{self._glob_escape(needle)}*'
        script = f"find {shlex.quote(directory)} -mindepth 1 -maxdepth {self.COMPLETE_MAX_DEPTH} \\( {ignores} \\) -prune -o -iname {shlex.quote(pattern)} -printf '%y\\0%s\\0%T@\\0%l\\0%P\\0' | head -c {self.COMPLETE_OUTPUT_BYTES}"
        result = self._raw(script)
        if result.exit_code != 0:
            return []
        fields = result.stdout.split('\0')
        base_path = self.relative(base)
        entries: list[Entry] = []
        for index in range(0, len(fields) - 4, 5):
            kind, size, mtime, target, rel = fields[index : index + 5]
            if not rel:
                continue
            path = posixpath.join(base_path, rel) if base_path else rel
            entries.append(Entry(name=posixpath.basename(rel), path=path, type=_TYPES.get(kind, 'other'), size=int(size or 0), mtime=float(mtime or 0), target=target or None))
        entries.sort(key=lambda entry: (entry.type != 'dir', entry.name.lower(), entry.path.lower()))
        return entries[:limit]

    def read_text_file(self, path: str | None, limit: int = REDACT_MAX_BYTES) -> tuple[bytes, Entry]:
        container_path = self.resolve(path)
        entry = self._stat(container_path)
        if entry.type == 'dir':
            raise IsDirectoryError(f'path is a directory: {entry.path}')
        if entry.size > limit:
            raise FileTooLargeError(f'file is too large to inspect: {entry.path}')
        return self._read_bytes(container_path), entry

    def open_file(self, path: str | None) -> tuple[Entry, Iterator[bytes]]:
        container_path = self.resolve(path)
        entry = self._stat(container_path)
        if entry.type == 'dir':
            raise IsDirectoryError(f'path is a directory: {entry.path}')
        if entry.size > WORKSPACE_MAX_BYTES:
            raise FileTooLargeError(f'file is too large: {entry.path}')
        container = self._ensure()
        try:
            stream, _ = container.get_archive(container_path)
        except NotFound as error:
            raise FileMissingError(f'path not found: {entry.path}') from error
        return entry, _tar_file_chunks(stream)

    def _tree_size(self, container_path: str) -> int:
        result = self._raw(f"find {shlex.quote(container_path)} -type f -printf '%s\\n'")
        if result.exit_code != 0:
            raise WorkspaceError(f'cannot measure path: {self._relative(container_path) or "."}')
        return sum(int(line) for line in result.stdout.splitlines() if line.isdigit())

    def archive(self, path: str | None) -> tuple[str, int, Iterator[bytes]]:
        container_path = self.resolve(path)
        entry = self._stat(container_path)
        total = entry.size if entry.type == 'file' else self._tree_size(container_path)
        if total > WORKSPACE_MAX_BYTES:
            raise FileTooLargeError(f'archive is too large: {entry.path or "."}')
        name = (posixpath.basename(container_path) if container_path != self.workdir else 'workspace') or 'workspace'
        container = self._ensure()
        try:
            stream, _ = container.get_archive(container_path)
        except NotFound as error:
            raise FileMissingError(f'path not found: {entry.path or "."}') from error
        return f'{name}.zip', total, _zip_chunks(stream)

    def _stage(self, name: str, source: IO[bytes], size: int) -> str:
        container = self._ensure()
        temp_dir = posixpath.join(self.WORKDIR, '.rqark-tmp', uuid.uuid4().hex)
        result = self._raw(f'mkdir -p {shlex.quote(temp_dir)}')
        if result.exit_code != 0:
            raise WorkspaceError(f'cannot stage upload: {result.stderr.strip()}')
        staged = posixpath.join(temp_dir, name)
        try:
            with tempfile.TemporaryFile() as buffer:
                with tarfile.open(fileobj=buffer, mode='w') as tar:
                    info = tarfile.TarInfo(name)
                    info.size = size
                    info.mtime = int(time.time())
                    info.mode = 0o644
                    reader = _SizedReader(source, size)
                    tar.addfile(info, reader)
                    if reader.remaining:
                        raise WorkspaceError(f'incomplete upload: {name}')
                buffer.seek(0)
                if not container.put_archive(temp_dir, buffer):
                    raise WorkspaceError(f'cannot write file: {name}')
        except BaseException:
            self._raw(f'rm -rf -- {shlex.quote(temp_dir)}')
            raise
        return staged

    def _commit(self, staged: str, target: str) -> None:
        result = self._raw(f'mv -f -- {shlex.quote(staged)} {shlex.quote(target)}')
        self._raw(f'rm -rf -- {shlex.quote(posixpath.dirname(staged))}')
        if result.exit_code != 0:
            raise WorkspaceError(f'cannot move file into place: {result.stderr.strip()}')

    def put_file(self, directory: str | None, name: str | None, source: IO[bytes], size: int, *, overwrite: bool = True, if_unmodified_since: float | None = None) -> Entry:
        if size > WORKSPACE_MAX_BYTES:
            raise FileTooLargeError(f'file is too large: {name}')
        directory_path = self.resolve(directory)
        parent = self._stat(directory_path)
        if parent.type != 'dir':
            raise PathNotDirectoryError(f'not a directory: {directory or "."}')
        safe = _safe_name(name)
        target = posixpath.join(directory_path, safe)
        existing = self._optional(target)
        if existing is not None:
            if existing.type == 'dir':
                raise IsDirectoryError(f'path is a directory: {existing.path}')
            if not overwrite:
                raise ConflictError(f'file already exists: {existing.path}')
            if if_unmodified_since is not None and int(existing.mtime) > int(if_unmodified_since):
                raise PreconditionFailedError(f'file changed since {int(if_unmodified_since)}: {existing.path}')
        staged = self._stage(safe, source, size)
        self._commit(staged, target)
        return self._stat(target)

    def put(self, path: str | None, source: IO[bytes], size: int, *, overwrite: bool = True, if_unmodified_since: float | None = None) -> Entry:
        raw = self.api_path(path)
        if raw == self.workdir:
            raise InvalidOperationError('target must be a file inside the workspace')
        directory = self._relative(posixpath.dirname(raw))
        return self.put_file('' if directory == '.' else directory, posixpath.basename(raw), source, size, overwrite=overwrite, if_unmodified_since=if_unmodified_since)

    def mkdir(self, path: str | None) -> Entry:
        raw = self.api_path(path)
        if raw == self.workdir:
            return self._stat(self.workdir)
        target = self.resolve(path, strict=False)
        existing = self._optional(target)
        if existing is not None and existing.type != 'dir':
            raise ConflictError(f'path already exists and is not a directory: {path}')
        result = self._raw(f'mkdir -p -- {shlex.quote(target)}')
        if result.exit_code != 0:
            raise WorkspaceError(f'cannot create directory: {result.stderr.strip()}')
        entry = self._stat(target)
        if entry.type != 'dir':
            raise InvalidOperationError(f'not a directory: {path}')
        self.resolve(path)
        return entry

    def move(self, path: str | None, new_path: str | None, *, overwrite: bool = False) -> Entry:
        source = self.resolve(path, strict=False)
        if source == self.workdir:
            raise InvalidOperationError('cannot move the workspace root')
        target = self.resolve(new_path, strict=False)
        if target == self.workdir:
            raise InvalidOperationError('cannot replace the workspace root')
        if source == target:
            return self._stat(source)
        existing = self._optional(target)
        if existing is not None and not overwrite:
            raise ConflictError(f'target already exists: {self._relative(target)}')
        if existing is not None and existing.type == 'dir':
            raise IsDirectoryError(f'target is a directory: {self._relative(target)}')
        result = self._raw(f'mv -f -- {shlex.quote(source)} {shlex.quote(target)}')
        if result.exit_code != 0:
            raise WorkspaceError(f'cannot move path: {result.stderr.strip()}')
        return self._stat(target)

    def delete(self, path: str | None) -> None:
        target = self.resolve(path, strict=False)
        if target == self.workdir:
            raise InvalidOperationError('cannot delete the workspace root')
        result = self._raw(f'rm -rf -- {shlex.quote(target)}')
        if result.exit_code != 0:
            raise WorkspaceError(f'cannot delete path: {result.stderr.strip()}')

    def _volume(self, client: docker.DockerClient):
        try:
            return client.volumes.get(self.name)
        except NotFound:
            pass
        try:
            return client.volumes.create(self.name)
        except APIError as error:
            if error.status_code != 409:
                raise
            return client.volumes.get(self.name)

    def _network(self, client: docker.DockerClient) -> str:
        labels = {'rqark.sandbox': '1', 'rqark.user': self.user_id}
        try:
            return str(client.networks.get(self.network_name).name)
        except NotFound:
            return str(client.networks.create(self.network_name, driver='bridge', labels=labels).name)

    def _connection(self, client: docker.DockerClient) -> dict[str, Any]:
        if self.network in self.RESERVED_NETWORKS:
            return {'network_mode': str(self.network)}
        if self.network:
            return {'network': self.network}
        return {'network': self._network(client)}

    def _create(self, client: docker.DockerClient, pulled: bool = False) -> Container:
        volume = self._volume(client)
        try:
            return client.containers.run(
                self.image,
                command='sleep infinity',
                name=self.container_name,
                detach=True,
                init=True,
                mem_limit=self.memory,
                memswap_limit=self.memory,
                nano_cpus=self.nano_cpus,
                pids_limit=self.pids_limit,
                cap_drop=['ALL'],
                cap_add=list(self.cap_add),
                security_opt=['no-new-privileges'],
                volumes={volume.name: {'bind': self.WORKDIR, 'mode': 'rw'}},
                working_dir=self.workdir,
                labels={'rqark.sandbox': '1', 'rqark.user': self.user_id},
                **self._connection(client),
            )
        except ImageNotFound:
            if pulled:
                raise
            client.images.pull(self.image)
            return self._create(client, pulled=True)
        except APIError as error:
            if error.status_code != 409:
                raise
            return client.containers.get(self.container_name)

    def _container(self, client: docker.DockerClient) -> Container:
        try:
            container = client.containers.get(self.container_name)
        except NotFound:
            return self._create(client)
        if container.status == 'running':
            return container
        try:
            container.start()
            return container
        except APIError, DockerException:
            try:
                container.remove(force=True)
            except APIError, DockerException:
                pass
            return self._create(client)

    def _ensure(self) -> Container:
        client = _client_instance()
        with _lock(self.container_name):
            container = self._container(client)
            if self._subdir and not self._workdir_ready:
                result = self._exec(container, f'mkdir -p -- {shlex.quote(self.workdir)}')
                if result.exit_code != 0:
                    raise WorkspaceError(f'cannot create project directory: {result.stderr.strip()}')
                self._workdir_ready = True
        return container

    def _exec(self, container: Container, script: str, cwd: str | None = None) -> ShellResult:
        stream = None
        try:
            api = _client_instance().api
            exec_id = api.exec_create(container.id, cmd=['/bin/sh', '-c', script], workdir=cwd or self.workdir)['Id']
            stream = api.exec_start(exec_id, stream=True, demux=True)
            stdout, stderr, truncated = _drain_capped(stream, WORKSPACE_SHELL_MAX_BYTES)
            info = api.exec_inspect(exec_id)
        except (APIError, DockerException, NotFound) as error:
            return ShellResult(-1, '', str(error))
        finally:
            _close_quietly(stream)
        exit_code = info.get('ExitCode')
        out = _text(stdout)
        if truncated:
            out += f'\n… (output truncated at {WORKSPACE_SHELL_MAX_BYTES} bytes)'
        return ShellResult(exit_code if exit_code is not None else -1, out, _text(stderr))

    def _raw(self, script: str, cwd: str | None = None) -> ShellResult:
        return self._exec(self._ensure(), script, cwd)

    def run_shell(self, command: str, cwd: str | None = None, timeout: int = 60, interpreter: str = '/bin/sh') -> ShellResult:
        return self._raw(f'exec timeout -k 5 {timeout} {interpreter} -c {shlex.quote(command)}', cwd)

    async def shell(self, command: str, cwd: str | None = None, timeout: int = 60, interpreter: str = '/bin/sh') -> ShellResult:
        return await asyncio.to_thread(self.run_shell, command, cwd, timeout, interpreter)

    async def read_bytes(self, path: str) -> bytes:
        return await asyncio.to_thread(self._read_bytes, path)

    def _read_bytes(self, path: str) -> bytes:
        container = self._ensure()
        try:
            stream, _ = container.get_archive(path)
        except NotFound as error:
            raise FileMissingError(f'file not found: {path}') from error
        try:
            with _open_archive(stream) as tar:
                for member in tar:
                    if member.isdir():
                        raise IsDirectoryError(f'path is a directory: {path}')
                    if not member.isfile():
                        continue
                    if member.size > WORKSPACE_MAX_BYTES:
                        raise FileTooLargeError(f'file is too large: {path}')
                    handle = tar.extractfile(member)
                    data = handle.read() if handle is not None else b''
                    if len(data) > WORKSPACE_MAX_BYTES:
                        raise FileTooLargeError(f'file is too large: {path}')
                    return data
        except tarfile.TarError as error:
            raise WorkspaceError(f'cannot read archive for {path}: {error}') from error
        finally:
            _close_quietly(stream)
        raise FileMissingError(f'file not found: {path}')

    async def write_bytes(self, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_bytes, path, data)

    def _write_bytes(self, path: str, data: bytes) -> None:
        container = self._ensure()
        parent = posixpath.dirname(path) or self.workdir
        self._raw(f'mkdir -p {shlex.quote(parent)}')
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as tar:
            info = tarfile.TarInfo(posixpath.basename(path))
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        if not container.put_archive(parent, buffer.getvalue()):
            raise WorkspaceError(f'cannot write file: {path}')

    async def ensure(self) -> None:
        await asyncio.to_thread(self._ensure)

    async def stop(self) -> None:
        await asyncio.to_thread(self._stop)

    def _stop(self) -> None:
        container = self._find()
        if container is None or container.status != 'running':
            return
        try:
            container.stop(timeout=5)
        except APIError, DockerException:
            pass

    async def remove(self) -> None:
        await asyncio.to_thread(self._remove)

    def _remove(self) -> None:
        container = self._find()
        if container is None:
            return
        try:
            container.remove(force=True)
        except APIError, DockerException:
            pass

    async def purge(self) -> None:
        await asyncio.to_thread(self._purge)

    def _purge(self) -> None:
        self._remove()
        try:
            client = _client_instance()
        except DockerException:
            return
        try:
            client.volumes.get(self.name).remove(force=True)
        except APIError, DockerException, NotFound:
            pass
        try:
            client.networks.get(self.network_name).remove()
        except APIError, DockerException, NotFound:
            pass

    def _find(self) -> Container | None:
        try:
            return _client_instance().containers.get(self.container_name)
        except NotFound:
            return None
