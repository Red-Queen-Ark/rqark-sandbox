from rqark_core.tools.git.github import GitHub as CoreGitHub

from rqark_sandbox.tools.git.base_git import SandboxGitTool


class GitHub(SandboxGitTool, CoreGitHub):
    pass
