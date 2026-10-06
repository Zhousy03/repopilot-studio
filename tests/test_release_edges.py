import importlib.util
from pathlib import Path
import pytest
from repopilot.config import Configuration
from repopilot.db import uid
from repopilot.security import path_ok


def load_script(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'scripts'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_retrieval_metric_deduplicates_files():
    m=load_script('evaluate_retrieval')
    assert m.score(['a','a','b'],['a','b','c'],3)=={'file_recall_at_k':2/3,'file_hit_at_k':1.0}
    with pytest.raises(ValueError):m.score([],[],5)


def test_no_fake_unlabelled_answer_accuracy():
    m=load_script('evaluate_agent')
    out=m.evaluate({'status':'done','answer':'hello'},[],{'question':'hi'})
    assert 'accuracy' not in out and 'substring_smoke_passed' not in out
    out=m.evaluate({'status':'done','answer':'abc'},[{'event':'tool_finished','data':{'action':{'tool':'search'}}}],
       {'required_tools':['search'],'forbidden_tools':['read_file'],'max_tool_calls':1,'required_substrings':['ab']})
    assert out['required_tools_present'] and out['forbidden_tool_absent'] and out['tool_budget_passed']


def test_settings_utf8_roundtrip(tmp_path):
    config=Configuration(tmp_path/'settings');config.update({'model':'中文模型'})
    assert Configuration(tmp_path/'settings').value.model=='中文模型'


@pytest.mark.parametrize('path',['foo?.py','bad*.py','a|b.py','a<b>.py','a"b.py'])
def test_cross_platform_unsafe_filenames(path):
    with pytest.raises(ValueError):path_ok(path)


def test_deleting_workspace_preserves_other_index_events(components,workspace):
    c,db,ws,_=components
    other=ws.ingest('other',{'hello.py':b'x=1\n'})
    first={'id':uid(),'workspace_id':workspace['id'],'status':'done'}
    second={'id':uid(),'workspace_id':other['id'],'status':'done'}
    db.put('index_job',first);db.put('index_job',second)
    db.event(first['id'],'progress',{'current':1});db.event(second['id'],'progress',{'current':1})
    ws.remove(workspace['id'])
    assert db.get('index_job',second['id']) and db.all_events(second['id'])
    with pytest.raises(KeyError):db.get('index_job',first['id'])


def test_json_duplicate_paths_rejected(client):
    r=client.post('/api/workspaces/text',json={'name':'x','files':[{'path':'a.py','content':'x=1'}, {'path':'a.py','content':'x=2'}]})
    assert r.status_code==400


def test_malformed_zip_is_400(client):
    r=client.post('/api/workspaces/zip',data={'name':'bad'},files={'archive':('bad.zip',b'not zip','application/zip')})
    assert r.status_code==400
