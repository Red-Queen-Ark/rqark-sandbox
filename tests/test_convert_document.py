import pytest

from rqark_sandbox.tools import WORKSPACE_TOOLS, convert_document
from rqark_sandbox.tools.convert_document import ConvertDocument
from rqark_sandbox.workspace import FileMissingError, FileTooLargeError, Workspace


class FakeWorkspace:
    WORKDIR = '/workspace'

    def __init__(self, files: dict[str, bytes] | None = None, error: Exception | None = None):
        self.files = files or {}
        self.error = error
        self.written: dict[str, bytes] = {}

    @property
    def workdir(self) -> str:
        return self.WORKDIR

    def to_container_path(self, path: str | None, default: str = '.') -> str:
        return Workspace('u1').to_container_path(path, default)

    async def read_bytes(self, path: str) -> bytes:
        if self.error:
            raise self.error
        if path not in self.files:
            raise FileMissingError(f'file not found: {path}')
        return self.files[path]

    async def write_bytes(self, path: str, data: bytes) -> None:
        self.written[path] = data


async def test_converts_docx_next_to_original(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_document, '_to_markdown', lambda raw, suffix: '# hi')
    workspace = FakeWorkspace({'/workspace/docs/a.docx': b'x'})
    result = await ConvertDocument(workspace).run(path='docs/a.docx')
    assert workspace.written == {'/workspace/docs/a.md': b'# hi'}
    assert '/workspace/docs/a.md' in result


async def test_custom_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_document, '_to_markdown', lambda raw, suffix: '# hi')
    workspace = FakeWorkspace({'/workspace/a.pdf': b'x'})
    await ConvertDocument(workspace).run(path='a.pdf', output='out/b.md')
    assert workspace.written == {'/workspace/out/b.md': b'# hi'}


async def test_rejects_unsupported() -> None:
    workspace = FakeWorkspace({'/workspace/a.txt': b'x'})
    result = await ConvertDocument(workspace).run(path='a.txt')
    assert 'unsupported' in result
    assert workspace.written == {}


async def test_missing_file() -> None:
    result = await ConvertDocument(FakeWorkspace()).run(path='gone.pdf')
    assert 'not found' in result


async def test_too_large() -> None:
    workspace = FakeWorkspace(error=FileTooLargeError('nope'))
    result = await ConvertDocument(workspace).run(path='big.pdf')
    assert 'too large' in result


async def test_conversion_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(raw: bytes, suffix: str) -> str:
        raise RuntimeError('bad file')

    monkeypatch.setattr(convert_document, '_to_markdown', boom)
    workspace = FakeWorkspace({'/workspace/a.pdf': b'x'})
    result = await ConvertDocument(workspace).run(path='a.pdf')
    assert 'bad file' in result
    assert workspace.written == {}


def test_registered_for_swapping() -> None:
    assert WORKSPACE_TOOLS['convert_document'] is ConvertDocument
