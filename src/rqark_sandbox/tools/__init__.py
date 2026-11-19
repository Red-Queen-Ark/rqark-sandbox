from collections.abc import Sequence

from rqark_core.tools.subagent import SubAgent
from rqark_core.types.tool import BaseTool

from rqark_sandbox.prompts import swap_prompts
from rqark_sandbox.tools.base import WorkspaceTool
from rqark_sandbox.tools.convert_document import ConvertDocument
from rqark_sandbox.tools.edit_file import EditFile
from rqark_sandbox.tools.exec import Exec
from rqark_sandbox.tools.git import GIT_TOOLS
from rqark_sandbox.tools.grep import Grep
from rqark_sandbox.tools.list_dir import ListDir
from rqark_sandbox.tools.read_file import ReadFile
from rqark_sandbox.tools.write_file import WriteFile
from rqark_sandbox.workspace import Workspace

__all__ = ['ConvertDocument', 'EditFile', 'Exec', 'Grep', 'ListDir', 'ReadFile', 'WorkspaceTool', 'WriteFile', 'build_workspace_tools', 'swap_tools']

WORKSPACE_TOOLS: dict[str, type[WorkspaceTool]] = {'exec': Exec, 'read_file': ReadFile, 'write_file': WriteFile, 'edit_file': EditFile, 'grep': Grep, 'list_dir': ListDir, 'convert_document': ConvertDocument}


def build_workspace_tools(workspace: Workspace) -> list[BaseTool]:
    return [Exec(workspace), ReadFile(workspace), WriteFile(workspace), EditFile(workspace), Grep(workspace), ListDir(workspace), ConvertDocument(workspace)]


def swap_tools(tools: Sequence[BaseTool], workspace: Workspace) -> list[BaseTool]:
    return [_swap(tool, workspace) for tool in tools]


def _swap(tool: BaseTool, workspace: Workspace) -> BaseTool:
    if isinstance(tool, SubAgent):
        return SubAgent(swap_prompts(tool.prompts, workspace), swap_tools(tool.tools, workspace))
    name = tool.name
    if name in WORKSPACE_TOOLS:
        return WORKSPACE_TOOLS[name](workspace)
    if name in GIT_TOOLS:
        return GIT_TOOLS[name](workspace, **tool.params())
    return tool
