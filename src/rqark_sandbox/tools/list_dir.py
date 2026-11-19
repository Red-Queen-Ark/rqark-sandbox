import shlex
from typing import Any

from rqark_core.types.tool import ToolContext

from rqark_sandbox.tools.base import WorkspaceTool
from rqark_sandbox.workspace import PathOutsideWorkspaceError

SKIP_DIRS: tuple[str, ...] = ('.git', 'node_modules', '.venv', 'venv', '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache', '.idea', '.vscode')

MISSING = 3
NOT_A_DIRECTORY = 4


def _format(entries: list[str]) -> str:
    directories = sorted((name for name in entries if name.endswith('/')), key=str.lower)
    files = sorted((name for name in entries if not name.endswith('/')), key=str.lower)
    lines = [*directories]
    if directories and files:
        lines.append('')
    lines.extend(files)
    return '\n'.join(lines)


class ListDir(WorkspaceTool):
    @property
    def name(self) -> str:
        return 'list_dir'

    @property
    def display_key(self) -> str:
        return 'path'

    def get_openai_schema(self) -> dict[str, Any]:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': 'List the entries of a directory in the sandbox workspace. Subdirectories are suffixed with a trailing slash.',
                'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'Directory path to list (defaults to workspace root)'}}, 'required': []},
            },
        }

    def _command(self, path: str) -> str:
        target = shlex.quote(path)
        return f'if [ ! -e {target} ]; then exit {MISSING}; fi\nif [ ! -d {target} ]; then exit {NOT_A_DIRECTORY}; fi\nls -1Ap -- {target}'

    async def run(self, ctx: ToolContext | None = None, **kwargs: Any) -> str:
        raw = str(kwargs.get('path') or '.')
        try:
            resolved = self.container_path(raw, default='.')
        except PathOutsideWorkspaceError as error:
            return self.error(error)
        result = await self.shell(self._command(resolved))
        if result.exit_code == MISSING:
            return f'Error: path not found: {raw}'
        if result.exit_code == NOT_A_DIRECTORY:
            return f'Error: not a directory: {raw}'
        if result.exit_code != 0:
            return f'Error: cannot list directory: {result.stderr.strip()}'
        entries = [line for line in result.stdout.splitlines() if (line[:-1] if line.endswith('/') else line) not in SKIP_DIRS]
        entries = [entry for entry in entries if entry != '']
        if not entries:
            return f'(empty directory: {raw})'
        return _format(entries)
