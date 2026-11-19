from rqark_core.tools.git.gitlab import GitLab as CoreGitLab

from rqark_sandbox.tools.git.base_git import SandboxGitTool


class GitLab(SandboxGitTool, CoreGitLab):
    pass
