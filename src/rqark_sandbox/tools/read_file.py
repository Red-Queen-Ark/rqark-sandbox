from typing import Any

from rqark_core.types.tool import ToolContext

from rqark_sandbox.tools.base import WorkspaceTool
from rqark_sandbox.workspace import WorkspaceError

DEFAULT_LIMIT = 2000


class ReadFile(WorkspaceTool):
    @property
    def name(self) -> str:
        return 'read_file'

    @property
    def display_key(self) -> str:
        return 'path'

    def get_openai_schema(self) -> dict[str, Any]:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': 'Read the contents of a text file from the sandbox workspace. Returns each line prefixed with its 1-indexed line number. Use offset/limit to read a slice of large files.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': 'Path to the file to read'},
                        'offset': {'type': 'integer', 'description': 'Line number to start from (1-indexed)'},
                        'limit': {'type': 'integer', 'description': 'Maximum number of lines to read'},
                    },
                    'required': ['path'],
                },
            },
        }

    async def run(self, ctx: ToolContext | None = None, **kwargs: Any) -> str:
        path = str(kwargs.get('path', ''))
        if not path:
            return 'Error: path is required'
        offset = int(kwargs.get('offset') or 1)
        limit = int(kwargs.get('limit') or DEFAULT_LIMIT)
        try:
            raw = await self.workspace.read_bytes(self.file_path(path))
        except WorkspaceError as error:
            return self.error(error)
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            return f'Error: cannot decode file as UTF-8: {path}'
        lines = text.splitlines()
        start = max(offset, 1)
        selected = lines[start - 1 : start - 1 + limit]
        if not selected:
            return f'(no content at offset {offset})'
        return '\n'.join(f'{start + index}: {line}' for index, line in enumerate(selected))
