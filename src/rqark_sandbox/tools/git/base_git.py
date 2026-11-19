import posixpath
import shlex
from typing import Any

from rqark_core.tools.git.base_git import BaseGitTool

from rqark_sandbox.workspace import Workspace

CLONE_TIMEOUT = 120
REPOS_DIR = 'repos'


class SandboxGitTool(BaseGitTool):
    def __init__(self, workspace: Workspace, token: str = '', base_url: str = '', username: str = ''):
        super().__init__(token=token, base_url=base_url, username=username)
        self.workspace = workspace

    def _dispatch(self, kwargs: dict[str, Any]) -> str:
        if str(kwargs.get('action', '')) == 'clone_repo':
            return self._clone_repo(kwargs)
        return super()._dispatch(kwargs)

    def _clone_repo(self, p: dict[str, Any]) -> str:
        repo = str(p.get('repo', ''))
        error = self._need(repo=repo)
        if error:
            return error
        base = posixpath.join(Workspace.WORKDIR, REPOS_DIR)
        target = posixpath.normpath(posixpath.join(base, repo))
        if target == base or not target.startswith(base + '/'):
            return f'Error: invalid repo name: {repo}'
        if self.workspace.run_shell(f'[ -e {shlex.quote(target)} ]').exit_code == 0:
            return f'Error: Directory already exists: {target}'
        try:
            url = self._clone_url(repo)
        except (ValueError, NotImplementedError) as error:
            return f'Error: {error}'
        self.workspace.run_shell(f'mkdir -p {shlex.quote(posixpath.dirname(target))}')
        result = self.workspace.run_shell(f'git clone {shlex.quote(url)} {shlex.quote(target)}', timeout=CLONE_TIMEOUT)
        if result.exit_code == 0:
            return f'Successfully cloned {repo} to {target}'
        if result.exit_code == 124:
            return f'Clone failed: operation timed out after {CLONE_TIMEOUT} seconds'
        if 'not found' in result.stderr and result.exit_code == 127:
            return 'Clone failed: git command not found in the sandbox image'
        stderr = result.stderr.replace(self._access_token, '***') if self._access_token else result.stderr
        return f'Clone failed: {stderr.strip()}'
