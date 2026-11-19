# rqark-sandbox

A docker workspace for `rqark-app` agents: file, shell and git tools that run inside a per-user container, plus the prompts that describe that container to the model.

## Why rqark-sandbox

`rqark-core` tools run in the host process. This one runs them next to the data: every user gets a named container with a mounted volume, and the agent's tools are swapped for workspace-backed versions that cannot leave `/workspace`.

## Installation

Requires Python 3.14+.

```bash
uv add rqark-sandbox
# or
pip install rqark-sandbox
```

## Quick Start

```python
from rqark_app.agent.agents import Wednesday
from rqark_sandbox import Workspace, swap_agent

workspace = Workspace('user-1', image='ubuntu:24.04')
await workspace.ensure()

agent = swap_agent(Wednesday(), workspace)
```

## What it ships

| Module | Contents |
| --- | --- |
| `rqark_sandbox.workspace` | `Workspace`: container lifecycle, file read/write, shell exec, archive, upload and download caps |
| `rqark_sandbox.project` | `load_project`: the project a session works in |
| `rqark_sandbox.tools` | `exec`, `read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `convert_document` and the git tools |
| `rqark_sandbox.prompts` | `AgentsMdPrompt`, `GitProjectPrompt`, `PlatformPrompt`, workspace prompt |
| `rqark_sandbox.swap_agent` | Replaces an agent's tools and prompts with the workspace-backed versions |

## Configuration

Environment variables read by the package:

| Variable | Default | Meaning |
| --- | --- | --- |
| `WORKSPACE_MAX_BYTES` | `104857600` | largest file the workspace will read or write |
| `WORKSPACE_SHELL_MAX_BYTES` | `8388608` | stdout and stderr cap for one shell call |

The container image is not an environment variable here: `Workspace` takes it as an argument.

The host needs access to the docker socket, and the image must exist locally.
