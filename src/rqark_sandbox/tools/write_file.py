from typing import Any

from rqark_core.types.tool import ToolContext

from rqark_sandbox.tools.base import WorkspaceTool
from rqark_sandbox.workspace import WorkspaceError


class WriteFile(WorkspaceTool):
    @property
    def name(self) -> str:
        return 'write_file'

    @property
    def display_key(self) -> str:
        return 'path'

    def get_openai_schema(self) -> dict[str, Any]:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': 'Write text content to a file in the sandbox workspace, overwriting any existing content. Parent directories are created automatically. For targeted edits use edit_file instead.',
                'parameters': {
                    'type': 'object',
                    'properties': {'path': {'type': 'string', 'description': 'Path to the file to write'}, 'content': {'type': 'string', 'description': 'The full text content to write'}},
                    'required': ['path', 'content'],
                },
            },
        }

    async def run(self, ctx: ToolContext | None = None, **kwargs: Any) -> str:
        path = str(kwargs.get('path', ''))
        if not path:
            return 'Error: path is required'
        content = str(kwargs.get('content', ''))
        try:
            await self.workspace.write_bytes(self.file_path(path), content.encode('utf-8'))
        except WorkspaceError as error:
            return self.error(error)
        return f'Successfully wrote {path}'
