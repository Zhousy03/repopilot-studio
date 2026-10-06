import zipfile,io
import pytest
from repopilot.retrieval import Index

def test_starts_empty(components): assert components[1].list('workspace')==[]

def test_ignore_secret_binary(components):
    c,d,ws,ch=components
    w=ws.ingest('own-code',{'src/main.py':b'x=1\n','.env':b'SECRET=foo','binary.png':b'\0abc',
        '.gitignore':b'ignored.py\n','ignored.py':b'x=2','src/.gitignore':b'private.py\n','src/private.py':b'x=3'})
    paths={x['path'] for x in ws.files(w['id'])}
    assert paths=={'src/main.py','.gitignore','src/.gitignore'}

def test_raw_secret_preserved_only_redacted_for_model(components):
    c,d,ws,ch=components
    text=b'password="this-is-a-secret"\n'
    w=ws.ingest('private',{'config.py':text})
    assert ws.source(w['id'],'config.py')['content'].encode()==text
    index=Index(ws,w['id'],w['snapshot_id'])
    assert 'this-is-a-secret' not in index.files['config.py']['content']

def test_source_tamper_detected(components,workspace):
    ws=components[2]; _,_,root=ws.read(workspace['id'])
    (root/'calc.py').write_text('changed')
    with pytest.raises(ValueError): ws.source(workspace['id'],'calc.py')

def test_symbol_and_evidence_lines(components,workspace):
    ws=components[2]; index=Index(ws,workspace['id'],workspace['snapshot_id'])
    assert index.find_symbols('add')[0]['name']=='add'
    docs=index.search('add')
    assert docs
    for e in docs:
        lines=ws.source(workspace['id'],e['path'])['content'].splitlines()
        assert '\n'.join(lines[e['start_line']-1:e['end_line']])==e['content']

def test_no_keyword_has_no_hits(components,workspace):
    idx=Index(components[2],workspace['id'],workspace['snapshot_id'])
    assert idx.search('extraterrestrialzebraxyz')==[]

def test_export_exact_contents(components,workspace):
    ws=components[2]
    with zipfile.ZipFile(io.BytesIO(ws.export(workspace['id']))) as z:
        assert z.read('calc.py').decode()==ws.source(workspace['id'],'calc.py')['content']

def test_case_collision(components):
    with pytest.raises(ValueError): components[2].ingest('case',{'foo.py':b'1','Foo.py':b'2'})

def test_cannot_cross_workspace_snapshot(components,workspace):
    ws=components[2]; other=ws.ingest('other',{'a.py':b'x=1'})
    with pytest.raises(ValueError): ws.read(workspace['id'],other['snapshot_id'])
