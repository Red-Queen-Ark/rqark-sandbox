from rqark_core.types.tool import BaseTool

from rqark_sandbox.workspace import ShellResult, Workspace, WorkspaceError

MAX_OUTPUT = 8000


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return f'{text[:MAX_OUTPUT]}\n… (truncated, total {len(text)} chars)'


def format_shell(result: ShellResult) -> str:
    stdout = _truncate(result.stdout)
    stderr = _truncate(result.stderr)
    parts = [f'exit code: {result.exit_code}']
    if stdout:
        parts.append(f'stdout:\n{stdout}')
    if stderr:
        parts.append(f'stderr:\n{stderr}')
    if not stdout and not stderr:
        parts.append('(no output)')
    return '\n'.join(parts)


class WorkspaceTool(BaseTool):
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def container_path(self, path: str | None, default: str = '.') -> str:
        return self.workspace.to_container_path(path, default)

    def file_path(self, path: str | None, default: str = '.') -> str:
        resolved = self.workspace.to_container_path(path, default)
        if resolved == self.workspace.workdir:
            raise WorkspaceError(f'path must point at a file: {path or default}')
        return resolved

    async def shell(self, command: str, cwd: str | None = None, timeout: int = 60, interpreter: str = '/bin/sh') -> ShellResult:
        return await self.workspace.shell(command, cwd, timeout, interpreter)

    def error(self, error: Exception) -> str:
        return f'Error: {error}'
