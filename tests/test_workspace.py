import pytest

from rqark_sandbox.workspace import FileMissingError, PathOutsideWorkspaceError, Workspace, is_sensitive, redact


def test_api_path_rejects_unsafe_paths():
    for path in ('/etc/passwd', '..', 'a/../../b', 'a\x00b'):
        with pytest.raises(PathOutsideWorkspaceError):
            Workspace('u1').api_path(path)
    assert Workspace('u1').api_path('') == '/workspace'
    assert Workspace('u1').api_path('docs/./a.txt') == '/workspace/docs/a.txt'


def test_api_path_is_relative_to_the_workspace():
    workspace = Workspace('u1')
    assert workspace.relative('docs/a.txt') == 'docs/a.txt'
    assert workspace.relative('') == ''


def test_sensitive_files_are_detected():
    assert is_sensitive('.git/config')
    assert is_sensitive('repo/.git/config')
    assert is_sensitive('.env')
    assert is_sensitive('project/.env.local')
    assert not is_sensitive('README.md')


def test_redact_hides_tokens_and_passwords():
    payload = b'[remote "origin"]\n\turl = https://x-access-token:ghp_abc@host/r.git\nPASSWORD=hunter2\n'
    output = redact(payload).decode()
    assert 'ghp_abc' not in output
    assert 'hunter2' not in output
    assert 'https://***@host/r.git' in output


def test_list_hides_dotfiles_but_accepts_show_hidden():
    workspace = Workspace('u1')
    output = 'd\x004096\x001700000000.0\x00\x00\x00f\x003\x001700000000.0\x00\x00.env\x00f\x003\x001700000000.0\x00\x00a.txt\x00'
    workspace.resolve = lambda path, strict=True: '/workspace'
    workspace._raw = lambda script, cwd=None: type('R', (), {'exit_code': 0, 'stdout': output, 'stderr': ''})()
    names = [item.name for item in workspace.list_dir('')]
    assert names == ['a.txt']
    names = [item.name for item in workspace.list_dir('', show_hidden=True)]
    assert names == ['.env', 'a.txt']


def test_list_sorts_directories_first():
    workspace = Workspace('u1')
    output = 'd\x004096\x001700000000.0\x00\x00\x00f\x003\x001700000000.0\x00\x00b.txt\x00d\x004096\x001700000000.0\x00\x00a\x00'
    workspace.resolve = lambda path, strict=True: '/workspace'
    workspace._raw = lambda script, cwd=None: type('R', (), {'exit_code': 0, 'stdout': output, 'stderr': ''})()
    assert [item.name for item in workspace.list_dir('')] == ['a', 'b.txt']


def test_list_rejects_a_file():
    workspace = Workspace('u1')
    workspace.resolve = lambda path, strict=True: '/workspace/a.txt'
    workspace._raw = lambda script, cwd=None: type('R', (), {'exit_code': 0, 'stdout': 'f\x003\x001700000000.0\x00\x00\x00', 'stderr': ''})()
    with pytest.raises(Exception):
        workspace.list_dir('a.txt')


def test_complete_splits_the_directory_from_the_needle():
    workspace = Workspace('u1')
    seen = {}

    def raw(script, cwd=None):
        seen['script'] = script
        return type('R', (), {'exit_code': 0, 'stdout': 'f\x003\x001700000000.0\x00\x00main.py\x00d\x004096\x001700000000.0\x00\x00src\x00', 'stderr': ''})()

    workspace.resolve = lambda path, strict=True: '/workspace/src'
    workspace._raw = raw
    entries = workspace.complete('src/ma')
    assert [item.path for item in entries] == ['src/src', 'src/main.py']
    assert '/workspace/src' in seen['script']
    assert '-iname' in seen['script']
    assert "'ma*'" in seen['script']


def test_complete_sorts_directories_first_and_honours_the_limit():
    workspace = Workspace('u1')
    output = 'f\x003\x001700000000.0\x00\x00main.py\x00d\x004096\x001700000000.0\x00\x00mine\x00f\x003\x001700000000.0\x00\x00more.py\x00'
    workspace.resolve = lambda path, strict=True: '/workspace'
    workspace._raw = lambda script, cwd=None: type('R', (), {'exit_code': 0, 'stdout': output, 'stderr': ''})()
    assert [item.path for item in workspace.complete('m')] == ['mine', 'main.py', 'more.py']
    assert [item.path for item in workspace.complete('m', limit=2)] == ['mine', 'main.py']


def test_complete_escapes_glob_metacharacters():
    workspace = Workspace('u1')
    seen = {}

    def raw(script, cwd=None):
        seen['script'] = script
        return type('R', (), {'exit_code': 0, 'stdout': '', 'stderr': ''})()

    workspace.resolve = lambda path, strict=True: '/workspace'
    workspace._raw = raw
    assert workspace.complete('a*b') == []
    assert '\\*b*' in seen['script']


def test_complete_returns_nothing_for_a_missing_directory():
    workspace = Workspace('u1')

    def missing(path, strict=True) -> str:
        raise FileMissingError(path)

    workspace.resolve = missing
    assert workspace.complete('nope/x') == []


def test_shell_output_is_capped_and_still_complete():
    from rqark_sandbox import workspace as ws

    chunks = [(b'a' * 6, None), (None, b'b' * 6), (b'c' * 6, None)]
    stdout, stderr, truncated = ws._drain_capped(iter(chunks), 10)

    assert stdout == b'aaaaaa'
    assert stderr == b'bbbb'
    assert truncated is True


def test_shell_output_under_the_cap_is_kept_whole():
    from rqark_sandbox import workspace as ws

    stdout, stderr, truncated = ws._drain_capped(iter([(b'ok', b'')]), 10)

    assert (stdout, stderr, truncated) == (b'ok', b'', False)


def test_an_archive_is_read_through_its_header_before_any_body():
    import io
    import tarfile

    from rqark_sandbox import workspace as ws

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        info = tarfile.TarInfo('a.txt')
        info.size = 5
        tar.addfile(info, io.BytesIO(b'hello'))
    data = buffer.getvalue()

    with ws._open_archive(iter([data[:512], data[512:]])) as tar:
        member = next(iter(tar))
        assert member.size == 5
        assert tar.extractfile(member).read() == b'hello'


def test_a_corrupt_archive_is_a_workspace_error_not_a_crash():
    from rqark_sandbox import workspace as ws

    class FakeContainer:
        def get_archive(self, path):
            return iter([b'not a tar archive at all']), {}

    workspace = ws.Workspace('u1')
    workspace._ensure = lambda: FakeContainer()

    with pytest.raises(ws.WorkspaceError):
        workspace._read_bytes('/workspace/a.txt')
