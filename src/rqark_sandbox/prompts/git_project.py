import asyncio
import shlex

from rqark_core.types.prompt import BasePrompt, PromptContext

from rqark_sandbox.workspace import ShellResult, Workspace


class GitProjectPrompt(BasePrompt):
    priority = 70

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    async def _git(self, args: list[str]) -> str | None:
        result = await self.workspace.shell(shlex.join(['git', *args]), cwd=self.workspace.workdir, timeout=10)
        if result.exit_code != 0:
            return None
        return result.stdout.strip() or None

    async def _is_git_project(self) -> bool:
        marker = shlex.quote(self.workspace.workdir + '/.git')
        result: ShellResult = await self.workspace.shell(f'[ -d {marker} ]')
        return result.exit_code == 0

    async def get_messages(self, ctx: PromptContext | None = None) -> str | None:
        if not await self._is_git_project():
            return None

        branch, head, status, log = await asyncio.gather(self._git(['branch', '--show-current']), self._git(['rev-parse', 'HEAD']), self._git(['status', '--porcelain']), self._git(['log', '--oneline', '-10', '--decorate']))

        parts: list[str] = ['## Git Project Context']

        if branch:
            parts.append(f'Current branch: {branch}')

        if head:
            parts.append(f'HEAD: {head}')

        if status:
            status_lines = status.split('\n')
            parts.append('Working tree status (porcelain):\n' + '\n'.join(status_lines[:40]))
            if len(status_lines) > 40:
                parts.append(f'... ({len(status_lines) - 40} more lines truncated)')
        else:
            parts.append('Working tree: clean')

        if log:
            parts.append(f'Recent commits:\n{log}')

        return '\n\n'.join(parts)
