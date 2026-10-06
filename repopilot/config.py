"""Local operator configuration. Secrets never appear in the public DTO."""
from __future__ import annotations
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, ConfigDict, model_validator


class UserSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: str = 'offline'
    base_url: str = ''
    model: str = ''
    api_key: str = ''
    backup_url: str = ''
    backup_model: str = ''
    backup_key: str = ''
    embedding_url: str = ''
    embedding_model: str = ''
    embedding_key: str = ''
    github_token: str = ''
    allow_source_upload: bool = False
    model_adapter: str = 'auto'
    native_tools: bool = False
    multi_agent: bool = True
    max_rounds: int = Field(default=3, ge=1, le=6)
    max_tool_calls: int = Field(default=12, ge=1, le=24)
    max_model_calls: int = Field(default=12, ge=2, le=30)
    max_output_tokens: int = Field(default=3500, ge=256, le=12000)
    timeout_seconds: int = Field(default=180, ge=20, le=600)
    request_timeout: int = Field(default=45, ge=5, le=120)
    tests_enabled: bool = False
    test_image: str = 'repopilot-sandbox:1'
    test_timeout: int = Field(default=120, ge=10, le=300)
    input_price_per_million: float = Field(default=0, ge=0, le=10000)
    output_price_per_million: float = Field(default=0, ge=0, le=10000)

    @model_validator(mode='after')
    def validate_fields(self):
        if self.model_adapter not in {'auto','http','langchain'}:
            raise ValueError('model_adapter 必须为 auto/http/langchain')
        if self.mode not in {'offline', 'openai'}:
            raise ValueError('mode 必须为 offline 或 openai')
        for key in ('base_url', 'backup_url', 'embedding_url'):
            value = getattr(self, key).rstrip('/')
            if value:
                u = urlsplit(value)
                if u.scheme not in {'http', 'https'} or not u.hostname or u.username or u.password or u.query or u.fragment:
                    raise ValueError(f'{key} 必须是无账号、无查询参数的 HTTP(S) Base URL')
                if u.scheme == 'http' and u.hostname not in {'localhost','127.0.0.1','::1'}:
                    raise ValueError('非本机模型必须使用 HTTPS')
                setattr(self, key, value)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@-]{0,160}', self.test_image):
            raise ValueError('测试镜像名称不合法')
        return self


SECRET_KEYS = ('api_key','backup_key','embedding_key','github_token')


class Configuration:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = (data_dir or Path(os.getenv('STUDIO_DATA_DIR', '.repopilot'))).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try: self.data_dir.chmod(0o700)
        except OSError: pass
        self.path = self.data_dir / 'settings.json'
        self.lock = threading.RLock()
        self.value = UserSettings.model_validate_json(self.path.read_text(encoding="utf-8")) if self.path.exists() else UserSettings()
        self.checkpoint_url = os.getenv('CHECKPOINT_DATABASE_URL', '')
        self.max_files = 3000
        self.max_file_bytes = 1_000_000
        self.max_upload_bytes = 32_000_000
        self.max_repo_bytes = 64_000_000
        self.max_workspace_count = 50
        self.max_versions = 50

    def public(self) -> dict:
        out = self.value.model_dump()
        for key in SECRET_KEYS:
            out[key + '_configured'] = bool(out.pop(key))
        out['keys_storage'] = '本机文件明文保存，操作系统权限限制；不是密钥保险库。'
        return out

    def preview(self, changes: dict | None = None) -> UserSettings:
        """Validate a form against saved settings without persisting its secrets."""
        with self.lock:
            return UserSettings.model_validate(self.value.model_dump() | (changes or {}))

    def update(self, changes: dict) -> dict:
        # Omitted key means keep. Explicit empty string means delete.
        with self.lock:
            candidate = self.preview(changes)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(candidate.model_dump_json(indent=2), encoding='utf-8')
            try: temp.chmod(0o600)
            except OSError: pass
            os.replace(temp, self.path)
            self.value = candidate
            return self.public()
