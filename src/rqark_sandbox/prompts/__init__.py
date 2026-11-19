from collections.abc import Callable, Sequence
from pathlib import Path

from rqark_core.prompts import WorkspacePrompt as CoreWorkspacePrompt
from rqark_core.types.prompt import BasePrompt

from rqark_sandbox.prompts.agents_md import AgentsMdPrompt
from rqark_sandbox.prompts.git_project import GitProjectPrompt
from rqark_sandbox.prompts.platform import PlatformPrompt
from rqark_sandbox.workspace import Workspace

__all__ = ['AgentsMdPrompt', 'GitProjectPrompt', 'PROMPTS', 'PlatformPrompt', 'swap_prompts']

PROMPTS: dict[str, Callable[[Workspace], BasePrompt]] = {
    'AgentsMdPrompt': AgentsMdPrompt,
    'GitProjectPrompt': GitProjectPrompt,
    'PlatformPrompt': lambda workspace: PlatformPrompt(),
    'WorkspacePrompt': lambda workspace: CoreWorkspacePrompt(Path(workspace.workdir)),
}


def swap_prompts(prompts: Sequence[BasePrompt], workspace: Workspace) -> list[BasePrompt]:
    return [_swap(prompt, workspace) for prompt in prompts]


def _swap(prompt: BasePrompt, workspace: Workspace) -> BasePrompt:
    replacement = PROMPTS.get(type(prompt).__name__)
    if replacement is None:
        return prompt
    return replacement(workspace)
