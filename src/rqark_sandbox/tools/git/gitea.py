from rqark_core.tools.git.gitea import Gitea as CoreGitea

from rqark_sandbox.tools.git.base_git import SandboxGitTool


class Gitea(SandboxGitTool, CoreGitea):
    pass
