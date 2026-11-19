from typing import Any

from rqark_core.types.tool import ToolContext

from rqark_sandbox.tools.base import WorkspaceTool, format_shell
from rqark_sandbox.workspace import PathOutsideWorkspaceError

MAX_COMMAND_LENGTH = 2000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300


class Exec(WorkspaceTool):
    @property
    def name(self) -> str:
        return 'exec'

    @property
    def display_key(self) -> str:
        return 'command'

    def get_openai_schema(self) -> dict[str, Any]:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': (
                    'Execute a shell command inside the sandbox workspace. '
                    'Returns exit code, stdout, and stderr. '
                    'ONLY use this for CLI tools, scripts, git, build commands, linters, and package managers. '
                    'DO NOT use for reading files (use read_file), listing directories (use list_dir), '
                    'searching file contents (use grep), or editing files (use edit_file/write_file). '
                    f'Commands are subject to a {DEFAULT_TIMEOUT}s timeout.'
                ),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'command': {'type': 'string', 'description': ('The shell command to execute. e.g. "ls -la" or "python script.py". Supports pipes and redirects via shell when metacharacters are present.')},
                        'working_dir': {'type': 'string', 'description': 'Optional working directory. Defaults to the workspace root.'},
                        'timeout': {'type': 'integer', 'description': f'Timeout in seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}).'},
                    },
                    'required': ['command'],
                },
            },
        }

    async def run(self, ctx: ToolContext | None = None, **kwargs: Any) -> str:
        command = str(kwargs.get('command', ''))
        if not command.strip():
            return 'Error: command is required'
        if len(command) > MAX_COMMAND_LENGTH:
            return f'Error: command too long ({len(command)} chars, max {MAX_COMMAND_LENGTH})'
        timeout = int(kwargs.get('timeout') or 0) or DEFAULT_TIMEOUT
        timeout = min(max(timeout, 1), MAX_TIMEOUT)
        try:
            cwd = self.container_path(str(kwargs.get('working_dir', '')) or None)
        except PathOutsideWorkspaceError as error:
            return self.error(error)
        return format_shell(await self.shell(command, cwd=cwd, timeout=timeout))
