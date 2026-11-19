import logging

from rqark_app.cloud.client import DataCloudError, RQArkDataCloudClient

logger = logging.getLogger(__name__)


async def load_project(client: RQArkDataCloudClient, project_id: object, *, session_id: str = '') -> dict | None:
    """The project a session works in, or None when it names none.

    A project that the session points at but the cloud no longer has is treated
    as no project: the conversation outlives the directory, so the turn runs at
    the volume root instead of failing.
    """
    if not project_id:
        return None
    try:
        return await client.get_project(str(project_id))
    except DataCloudError as error:
        if error.status_code != 404:
            raise
        logger.warning('project not found project=%s session=%s', project_id, session_id)
        return None


def project_subdir(project: dict | None) -> str:
    """The directory a project names, relative to the workspace root."""
    if not project:
        return ''
    return str(project.get('project_path') or '')
