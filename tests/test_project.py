import asyncio

import pytest
from rqark_core.prompts import AgentsMdPrompt, GitProjectPrompt, WorkspacePrompt
from rqark_core.types.prompt import BasePrompt

from rqark_sandbox.prompts import swap_prompts
from rqark_sandbox.prompts.agents_md import AgentsMdPrompt as SandboxAgentsMdPrompt
from rqark_sandbox.prompts.git_project import GitProjectPrompt as SandboxGitProjectPrompt
from rqark_sandbox.workspace import PathOutsideWorkspaceError, Workspace


class ShellWorkspace(Workspace):
    """A Workspace whose shell answers are scripted, so prompts can be tested
    without a container."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None, **kwargs):
        super().__init__('u1', **kwargs)
        self.answers = answers or {}

    def _raw(self, script, cwd=None):  # type: ignore[override]
        for needle, answer in self.answers.items():
            if needle in script:
                return _result(*answer)
        return _result(1, '')


def _result(exit_code: int, stdout: str):
    from rqark_sandbox.workspace import ShellResult

    return ShellResult(exit_code, stdout, '')


def test_a_project_moves_the_working_directory_below_the_root():
    workspace = Workspace('u1', subdir='acme/api')

    assert workspace.WORKDIR == '/workspace'
    assert workspace.workdir == '/workspace/acme/api'


def test_no_project_is_the_root_itself():
    workspace = Workspace('u1')

    assert workspace.workdir == workspace.WORKDIR


def test_relative_paths_resolve_inside_the_project():
    workspace = Workspace('u1', subdir='acme/api')

    assert workspace.api_path('') == '/workspace/acme/api'
    assert workspace.api_path('src/main.py') == '/workspace/acme/api/src/main.py'
    assert workspace.relative('src/main.py') == 'src/main.py'
    assert workspace.relative('') == ''


def test_a_project_cannot_escape_the_volume():
    with pytest.raises(PathOutsideWorkspaceError):
        Workspace('u1', subdir='../etc')
    with pytest.raises(PathOutsideWorkspaceError):
        Workspace('u1', subdir='/etc')
    with pytest.raises(PathOutsideWorkspaceError):
        Workspace('u1', subdir='acme/../../etc')
    with pytest.raises(PathOutsideWorkspaceError):
        Workspace('u1', subdir='acme\x00api')


def test_a_messy_project_path_is_normalised():
    assert Workspace('u1', subdir='  acme//api/  ').subdir == 'acme/api'
    assert Workspace('u1', subdir='acme/./api').subdir == 'acme/api'
    assert Workspace('u1', subdir='').subdir == ''
    assert Workspace('u1', subdir=None).subdir == ''


def test_an_absolute_path_in_the_tools_stays_confined_to_the_volume():
    workspace = Workspace('u1', subdir='acme')

    assert workspace.to_container_path('/workspace/acme/x', '.') == '/workspace/acme/x'
    assert workspace.to_container_path('x', '.') == '/workspace/acme/x'
    with pytest.raises(PathOutsideWorkspaceError):
        workspace.to_container_path('/tmp/x', '.')


def test_the_workspace_prompt_names_the_project_directory():
    workspace = ShellWorkspace(subdir='acme/api')
    prompts = swap_prompts([WorkspacePrompt()], workspace)

    assert isinstance(prompts[0], WorkspacePrompt)
    message = asyncio.run(prompts[0].get_messages())
    assert message == '## Workspace\nCurrent workspace: /workspace/acme/api'


def test_the_agents_md_prompt_reads_the_project_copy():
    workspace = ShellWorkspace({'/workspace/acme/api/AGENTS.md': (0, 'run the tests')}, subdir='acme/api')
    prompts = swap_prompts([AgentsMdPrompt()], workspace)

    assert isinstance(prompts[0], SandboxAgentsMdPrompt)
    message = asyncio.run(prompts[0].get_messages())
    assert message == '## Project Instructions (AGENTS.md)\n\nrun the tests'


def test_the_git_prompt_asks_about_the_project_repository():
    workspace = ShellWorkspace({'/workspace/acme/api/.git': (0, ''), 'branch --show-current': (0, 'main')}, subdir='acme/api')
    prompts = swap_prompts([GitProjectPrompt()], workspace)

    assert isinstance(prompts[0], SandboxGitProjectPrompt)
    message = asyncio.run(prompts[0].get_messages())
    assert message is not None
    assert 'Current branch: main' in message


def test_an_unknown_prompt_is_left_alone():
    class Other(BasePrompt):
        async def get_messages(self, ctx=None) -> str:
            return 'other'

    prompt = Other()
    assert swap_prompts([prompt], Workspace('u1'))[0] is prompt
