import pytest
from repopilot.models import Edit,ChangeSet
from repopilot.changes import static_check

def spec(old='return a - b',new='return a + b'):
    return ChangeSet(title='Fix addition',edits=[Edit(path='calc.py',old_text=old,new_text=new)])

def test_apply_is_real_and_keeps_original_snapshot(components,workspace):
    c,db,ws,changes=components
    p=changes.create(workspace['id'],workspace['snapshot_id'],spec())
    assert 'return a + b' in p['diff']
    assert 'return a - b' in ws.source(workspace['id'],'calc.py')['content']
    result=changes.decide(p['id'],p['digest'],True)
    assert result['status']=='applied'
    assert 'return a + b' in ws.source(workspace['id'],'calc.py')['content']
    assert 'return a - b' in ws.source(workspace['id'],'calc.py',workspace['snapshot_id'])['content']
    count=len(db.list('snapshot'))
    assert changes.decide(p['id'],p['digest'],True)['applied_snapshot']==result['applied_snapshot']
    assert len(db.list('snapshot'))==count

def test_rejection_does_not_change_code(components,workspace):
    _,_,ws,changes=components
    p=changes.create(workspace['id'],workspace['snapshot_id'],spec())
    changes.decide(p['id'],p['digest'],False)
    assert ws.read(workspace['id'])[0]['snapshot_id']==workspace['snapshot_id']

def test_stale_base_cannot_apply(components,workspace):
    _,_,ws,ch=components
    p=ch.create(workspace['id'],workspace['snapshot_id'],spec())
    q=ch.create(workspace['id'],workspace['snapshot_id'],spec(new='return a * b'))
    ch.decide(p['id'],p['digest'],True)
    with pytest.raises(ValueError,match='基线'): ch.decide(q['id'],q['digest'],True)

def test_digest_required(components,workspace):
    ch=components[3]; p=ch.create(workspace['id'],workspace['snapshot_id'],spec())
    with pytest.raises(ValueError): ch.decide(p['id'],'0'*64,True)

def test_ambiguous_edit_rejected(components):
    _,_,ws,ch=components; w=ws.ingest('x',{'calc.py':b'x=1\nx=1\n'})
    with pytest.raises(ValueError,match='精确匹配'): ch.create(w['id'],w['snapshot_id'],spec('x=1','x=2'))

def test_hash_guard(components,workspace):
    _,_,ws,ch=components
    s=ChangeSet(title='bad hash',edits=[Edit(path='calc.py',operation='replace_file',new_text='x=1',expected_hash='a'*64)])
    with pytest.raises(ValueError,match='哈希'): ch.create(workspace['id'],workspace['snapshot_id'],s)

def test_create_delete_and_revert(components,workspace):
    _,_,ws,ch=components
    s=ChangeSet(title='add file',edits=[Edit(path='new.py',operation='create',new_text='x=3\n')])
    p=ch.create(workspace['id'],workspace['snapshot_id'],s); ch.decide(p['id'],p['digest'],True)
    assert ws.source(workspace['id'],'new.py')['content']=='x=3\n'
    q=ch.inverse(p['id']); ch.decide(q['id'],q['digest'],True)
    with pytest.raises(KeyError): ws.source(workspace['id'],'new.py')

def test_redaction_placeholder_rejected(components,workspace):
    with pytest.raises(ValueError,match='脱敏'):
        components[3].create(workspace['id'],workspace['snapshot_id'],spec(new='[REDACTED_LITERAL]'))

def test_ast_check_is_not_test_execution():
    result=static_check({'bad.py':b'def broken(:\n','a.json':b'{}','x.toml':b'a=1\n'})
    assert result['executed_code'] is False
    assert result['status']=='failed'
    assert result['checked']==3

def test_crlf_preserved(components):
    _,_,ws,ch=components; w=ws.ingest('crlf',{'calc.py':b'def f():\r\n    return 1\r\n'})
    p=ch.create(w['id'],w['snapshot_id'],spec('return 1','return 2')); ch.decide(p['id'],p['digest'],True)
    assert ws.content_map(w['id'])['calc.py']==b'def f():\r\n    return 2\r\n'

@pytest.mark.parametrize('before,after',[(b'x=1',b'x=2'),(b'x=1\r\n',b'x=2\r\n'),(None,b''),(b'',None),(b'x=1\n',b'x=3\n')])
def test_export_patch_is_git_applicable(tmp_path,before,after):
    import subprocess,shutil
    from repopilot.changes import unified_patch
    if not shutil.which('git'):pytest.skip('git is required to verify patch compatibility')
    (tmp_path/'src').mkdir()
    file=tmp_path/'src'/'file with space.py'
    if before is not None:file.write_bytes(before)
    patch=unified_patch(before,after,'src/file with space.py')
    result=subprocess.run(['git','-c','core.autocrlf=false','apply','--unsafe-paths','-'],input=patch.encode(),cwd=tmp_path,capture_output=True)
    assert result.returncode==0,(patch,result.stderr.decode())
    if after is None:assert not file.exists()
    else:assert file.read_bytes()==after
