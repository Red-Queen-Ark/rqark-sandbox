import re
import shlex
from typing import Any

from rqark_core.types.tool import ToolContext

from rqark_sandbox.tools.base import WorkspaceTool
from rqark_sandbox.workspace import PathOutsideWorkspaceError

SKIP_DIRS: tuple[str, ...] = ('.git', 'node_modules', '.venv', 'venv', '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache', '.idea', '.vscode')
MAX_MATCHES = 200

GNU_GREP_MISSING = 'grep: unrecognized option'


class Grep(WorkspaceTool):
    @property
    def name(self) -> str:
        return 'grep'

    @property
    def display_key(self) -> str:
        return 'pattern'

    def get_openai_schema(self) -> dict[str, Any]:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': 'Search file contents for a regular expression inside the sandbox workspace. Returns matching lines as path:line_number:content. Optionally restrict to files matching a glob include pattern.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'pattern': {'type': 'string', 'description': 'Regular expression to search for'},
                        'path': {'type': 'string', 'description': 'File or directory to search (defaults to workspace root)'},
                        'include': {'type': 'string', 'description': 'Glob pattern to filter files, e.g. *.py'},
                    },
                    'required': ['pattern'],
                },
            },
        }

    def _command(self, pattern: str, include: str | None, path: str) -> str:
        args = ['grep', '-rInE', '--binary-files=without-match']
        args.extend(f'--exclude-dir={name}' for name in SKIP_DIRS)
        if include:
            args.append(f'--include={include}')
        args.extend(['-e', pattern, '--', path])
        return f'set -o pipefail; {" ".join(shlex.quote(arg) for arg in args)} | head -n {MAX_MATCHES + 1}'

    async def run(self, ctx: ToolContext | None = None, **kwargs: Any) -> str:
        pattern = str(kwargs.get('pattern', ''))
        if not pattern:
            return 'Error: pattern is required'
        try:
            re.compile(pattern)
        except re.error as error:
            return f'Error: invalid regex: {error}'
        include = str(kwargs['include']) if kwargs.get('include') else None
        raw = str(kwargs.get('path') or '.')
        try:
            resolved = self.container_path(raw, default='.')
        except PathOutsideWorkspaceError as error:
            return self.error(error)
        result = await self.shell(self._command(pattern, include, resolved), interpreter='/bin/bash')
        if result.exit_code == 2:
            if GNU_GREP_MISSING in result.stderr:
                return 'Error: GNU grep with -E/--binary-files support is required inside the sandbox image'
            return f'Error: {result.stderr.strip()}'
        lines = result.stdout.splitlines()
        if len(lines) > MAX_MATCHES:
            lines = [*lines[:MAX_MATCHES], f'... (truncated at {MAX_MATCHES} matches)']
        if not lines:
            return f'No matches for: {pattern}'
        return '\n'.join(lines)
