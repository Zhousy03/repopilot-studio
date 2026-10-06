import io
import stat
import zipfile
import pytest
from repopilot.security import path_ok,github_repo,redact

@pytest.mark.parametrize('path',['../x.py','/a.py','a/../x.py','a\\b.py','C:/x.py','.git/config','x/.env','id_rsa','nul.py','con/x.py','a\x00.py','a//b.py','./x.py'])
def test_unsafe_paths(path):
    with pytest.raises(ValueError): path_ok(path)

@pytest.mark.parametrize('path',['src/main.py','README.md','.github/workflows/test.yml','.gitignore','Dockerfile'])
def test_supported_paths(path): assert path_ok(path)==path

@pytest.mark.parametrize('url',['http://github.com/a/b','https://github.com.evil/a/b','https://user@github.com/a/b','https://github.com/a/b/tree/main','https://127.0.0.1/a/b'])
def test_github_url_scope(url):
    with pytest.raises(ValueError): github_repo(url)

def test_github_url(): assert github_repo('https://github.com/owner/repo.git')==('owner','repo')

def test_secret_redaction():
    assert 'abcdefghijklmnopqrstu' not in redact('api_key="abcdefghijklmnopqrstu"')

def test_zip_traversal(components):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z: z.writestr('../evil.py','print(1)')
    with pytest.raises(ValueError): components[2].zip_entries(raw.getvalue())

def test_zip_symlink(components):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        item=zipfile.ZipInfo('root/link.py'); item.external_attr=(stat.S_IFLNK|0o777)<<16
        z.writestr(item,'/etc/passwd')
    with pytest.raises(ValueError): components[2].zip_entries(raw.getvalue())

def test_zip_unwrap(components):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        z.writestr('project/a.py','x=1\n'); z.writestr('project/src/b.py','y=2\n')
    assert set(components[2].zip_entries(raw.getvalue()))=={'a.py','src/b.py'}
