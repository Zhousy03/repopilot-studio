from repopilot.security import path_ok


def test_extensionless_repository_documents_import(components):
    _, _, ws, _ = components
    documents = {'README': b'Hello world\n', 'LICENSE': b'License\n',
                 'NOTICE': b'Copyright\n', '.gitattributes': b'* text=auto\n'}
    workspace = ws.ingest('document-only', documents)
    assert ws.content_map(workspace['id']) == documents
    assert path_ok('docs/readme') == 'docs/readme'
