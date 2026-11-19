from rqark_app.types.agent import BaseAgent

from rqark_sandbox.prompts import swap_prompts
from rqark_sandbox.tools import swap_tools
from rqark_sandbox.workspace import FileMissingError, IsDirectoryError, PathOutsideWorkspaceError, ShellResult, Workspace, WorkspaceError

__all__ = ['FileMissingError', 'IsDirectoryError', 'PathOutsideWorkspaceError', 'ShellResult', 'Workspace', 'WorkspaceError', 'swap_agent', 'swap_prompts', 'swap_tools']


def swap_agent(agent: BaseAgent, workspace: Workspace) -> BaseAgent:
    agent.prompts = swap_prompts(agent.prompts, workspace)
    agent.tools = swap_tools(agent.tools, workspace)
    return agent
