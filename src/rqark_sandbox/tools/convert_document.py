import asyncio
import io
import posixpath
from typing import Any

from rqark_core.types.tool import ToolContext

from rqark_sandbox.tools.base import WorkspaceTool
from rqark_sandbox.workspace import FileTooLargeError, WorkspaceError

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({'.docx', '.pdf'})
MAX_SOURCE_BYTES = 32 * 1024 * 1024


class ConvertDocument(WorkspaceTool):
    @property
    def name(self) -> str:
        return 'convert_document'

    @property
    def display_key(self) -> str:
        return 'path'

    def get_openai_schema(self) -> dict[str, Any]:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': 'Convert a Word (DOCX) or PDF file in the sandbox workspace to Markdown. Writes a .md file next to the original and returns its path.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': 'Path to the DOCX or PDF file'},
                        'output': {'type': 'string', 'description': 'Optional output path for the .md file. Defaults to the original path with a .md suffix'},
                    },
                    'required': ['path'],
                },
            },
        }

    async def run(self, ctx: ToolContext | None = None, **kwargs: Any) -> str:
        path = str(kwargs.get('path', ''))
        if not path:
            return 'Error: path is required'
        source = self.file_path(path)
        suffix = posixpath.splitext(source)[1].lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return f'Error: unsupported file type: {suffix or "(none)"}. Only .docx and .pdf are supported'
        output = kwargs.get('output')
        target = self.file_path(str(output)) if output else f'{source[: -len(suffix)]}.md'
        try:
            raw = await self.workspace.read_bytes(source)
        except FileTooLargeError:
            return f'Error: file is too large to convert: {path}'
        except WorkspaceError as error:
            return self.error(error)
        if len(raw) > MAX_SOURCE_BYTES:
            return f'Error: file is too large to convert: {path}'
        try:
            text = await asyncio.to_thread(_to_markdown, raw, suffix)
        except Exception as error:
            return f'Error: failed to convert {path}: {error}'
        try:
            await self.workspace.write_bytes(target, text.encode('utf-8'))
        except WorkspaceError as error:
            return self.error(error)
        return f'Converted to {target}'


def _to_markdown(raw: bytes, suffix: str) -> str:
    from markitdown import MarkItDown, StreamInfo

    result = MarkItDown().convert_stream(io.BytesIO(raw), stream_info=StreamInfo(extension=suffix))
    return result.markdown
