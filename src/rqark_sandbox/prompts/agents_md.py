import shlex

from rqark_core.types.prompt import BasePrompt, PromptContext

from rqark_sandbox.workspace import Workspace

AGENTS_MD = 'AGENTS.md'


class AgentsMdPrompt(BasePrompt):
    priority = 50

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    async def get_messages(self, ctx: PromptContext | None = None) -> str | None:
        path = f'{self.workspace.workdir}/{AGENTS_MD}'
        result = await self.workspace.shell(f'cat -- {shlex.quote(path)}')
        if result.exit_code != 0:
            return None
        return f'## Project Instructions (AGENTS.md)\n\n{result.stdout.strip()}'
