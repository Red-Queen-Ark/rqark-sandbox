import difflib
from typing import Any

from rqark_core.types.tool import ToolContext

from rqark_sandbox.tools.base import WorkspaceTool
from rqark_sandbox.workspace import WorkspaceError

MIN_RATIO = 0.5


def _split_lines(text: str) -> list[str]:
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines = lines[:-1]
    return lines


def _find_best_match(target: list[str], search: list[str]) -> tuple[int, float]:
    window = len(search)
    if window == 0 or window > len(target):
        return -1, 0.0
    for index in range(len(target) - window + 1):
        if target[index : index + window] == search:
            return index, 1.0
    stripped_target = [line.strip() for line in target]
    stripped_search = [line.strip() for line in search]
    for index in range(len(stripped_target) - window + 1):
        if stripped_target[index : index + window] == stripped_search:
            return index, 1.0
    best_ratio, best_position = 0.0, -1
    for index in range(len(target) - window + 1):
        ratio = difflib.SequenceMatcher(None, search, target[index : index + window]).ratio()
        if ratio > best_ratio:
            best_ratio, best_position = ratio, index
    if best_ratio > MIN_RATIO:
        return best_position, best_ratio
    return -1, best_ratio


def _not_found_message(target: list[str], search: list[str], path: str, position: int, ratio: float) -> str:
    if ratio > MIN_RATIO and position >= 0:
        window = len(search)
        diff = '\n'.join(difflib.unified_diff(search, target[position : position + window], fromfile='old_string (provided)', tofile=f'{path} (actual, line {position + 1})', lineterm=''))
        return f'Error: old_string not found in {path}.\nBest match ({ratio:.0%} similar) at line {position + 1}:\n{diff}'
    return f'Error: old_string not found in {path}. No similar text found. Verify the file content.'


def _find_substring(content: str, old: str) -> tuple[str | None, int]:
    if old in content:
        return old, content.count(old)
    old_lines = _split_lines(old)
    if not old_lines:
        return None, 0
    stripped_old = [line.strip() for line in old_lines]
    content_lines = _split_lines(content)
    candidates: list[str] = []
    for index in range(len(content_lines) - len(stripped_old) + 1):
        window = content_lines[index : index + len(stripped_old)]
        if [line.strip() for line in window] == stripped_old:
            candidates.append('\n'.join(window))
    if candidates:
        return candidates[0], len(candidates)
    return None, 0


def edit_content(content: str, old_string: str, new_string: str, replace_all: bool, path: str) -> tuple[str | None, str]:
    match, count = _find_substring(content, old_string)
    if match is None:
        lines = content.split('\n')
        search_lines = old_string.split('\n')
        position, ratio = _find_best_match(lines, search_lines)
        return None, _not_found_message(lines, search_lines, path, position, ratio)
    if count > 1 and not replace_all:
        return None, f'Warning: old_string appears {count} times. Provide more context to make it unique, or set replace_all=true.'
    return content.replace(match, new_string, 1 if not replace_all else count), ''


class EditFile(WorkspaceTool):
    @property
    def name(self) -> str:
        return 'edit_file'

    @property
    def display_key(self) -> str:
        return 'path'

    def get_openai_schema(self) -> dict[str, Any]:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': (
                    'Perform exact string replacements in a file in the sandbox workspace. When editing text, ensure you preserve the exact indentation (tabs/spaces) as it appears before. Use replace_all to change every instance. Never run two edits on the same file in parallel — they race and one edit is lost.'
                ),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': 'The path to the file to modify.'},
                        'old_string': {'type': 'string', 'description': 'The text to replace. Must match exactly including whitespace.'},
                        'new_string': {'type': 'string', 'description': 'The text to replace it with (must be different from old_string).'},
                        'replace_all': {'type': 'boolean', 'description': 'Replace all occurrences of old_string (default false).'},
                    },
                    'required': ['path', 'old_string', 'new_string'],
                },
            },
        }

    async def run(self, ctx: ToolContext | None = None, **kwargs: Any) -> str:
        path = str(kwargs.get('path', ''))
        old_string = str(kwargs.get('old_string', ''))
        new_string = str(kwargs.get('new_string', ''))
        replace_all = bool(kwargs.get('replace_all', False))
        if not path:
            return 'Error: path is required'
        if not old_string:
            return 'Error: old_string is required'
        if new_string == old_string:
            return 'Error: new_string must be different from old_string'
        try:
            raw = await self.workspace.read_bytes(self.file_path(path))
        except WorkspaceError as error:
            return self.error(error)
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            return f'Error: cannot decode file as UTF-8: {path}'
        updated, message = edit_content(content, old_string, new_string, replace_all, path)
        if updated is None:
            return message
        try:
            await self.workspace.write_bytes(self.file_path(path), updated.encode('utf-8'))
        except WorkspaceError as error:
            return self.error(error)
        return f'Successfully edited {path}'
