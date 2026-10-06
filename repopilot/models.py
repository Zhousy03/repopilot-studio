from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)


class GithubImport(Input):
    url: str = Field(min_length=10, max_length=500)
    ref: str = Field(default='', max_length=200)


class FileInput(Input):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=1_000_000)


class FolderImport(Input):
    name: str = Field(min_length=1, max_length=100)
    files: list[FileInput] = Field(min_length=1, max_length=3000)


class Ask(Input):
    workspace_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    question: str = Field(min_length=1, max_length=12000)
    session_id: str = Field(default='', max_length=80, pattern=r'^[a-zA-Z0-9_-]*$')
    intent: Literal['investigate', 'change', 'review'] = 'investigate'
    urls: list[str] = Field(default_factory=list, max_length=4)


class Action(Input):
    tool: Literal['search', 'read_file', 'symbols', 'read_issue', 'read_docs']
    query: str = Field(default='', max_length=1500)
    path: str = Field(default='', max_length=500)
    start: int = Field(default=1, ge=1, le=100000)
    end: int = Field(default=80, ge=1, le=100000)
    url: str = Field(default='', max_length=1000)


class Plan(Input):
    actions: list[Action] = Field(default_factory=list, max_length=4)
    sufficient: bool = False
    reason: str = Field(default='', max_length=1000)


class Edit(Input):
    path: str = Field(min_length=1, max_length=500)
    operation: Literal['replace','create','delete','replace_file'] = 'replace'
    old_text: str = Field(default='', max_length=300000)
    new_text: str = Field(default='', max_length=500000)
    expected_hash: str = Field(default='', max_length=64)


class ChangeSet(Input):
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(default='', max_length=6000)
    edits: list[Edit] = Field(min_length=1, max_length=20)


class ManualProposal(ChangeSet):
    workspace_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{16,64}$')


class Decision(Input):
    approved: bool
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class TestRequest(Input):
    workspace_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{16,64}$')
    profile: Literal['static','pytest','unittest','node-test'] = 'static'
    approved: bool = False


class PublishRequest(Input):
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmation: Literal['CREATE_DRAFT_PR']
