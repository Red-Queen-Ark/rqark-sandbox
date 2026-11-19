from rqark_core.types.prompt import BasePrompt, PromptContext


class PlatformPrompt(BasePrompt):
    priority = 30

    async def get_messages(self, ctx: PromptContext | None = None) -> str:
        return '## Platform\nYou are running on Linux inside a Docker container. Use Unix-style paths with forward slashes, and prefer sh/bash syntax for shell commands. The exec tool runs commands with /bin/sh.'
