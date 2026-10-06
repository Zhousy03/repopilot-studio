"""Fixed Docker test profiles. No host-shell fallback. Never mounts Docker socket.
Containers reduce risk; they are not a guarantee against malicious code/kernel flaws.
"""
from __future__ import annotations
import asyncio
import contextlib
import os
import shutil
import time
from pathlib import Path
from .ai import Emit,noop
from .config import UserSettings
from .db import uid
from .security import redact

PROFILES={'pytest','unittest','node-test'}


class Sandbox:
    def __init__(self,settings: UserSettings): self.s=settings.model_copy(deep=True)

    async def availability(self) -> dict:
        binary=shutil.which('docker')
        if not binary: return {'docker':False,'image':False,'reason':'未安装 Docker CLI；动态测试不会在宿主机回退执行'}
        async def cmd(*args):
            p=await asyncio.create_subprocess_exec(binary,*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try: out,err=await asyncio.wait_for(p.communicate(),8)
            except asyncio.TimeoutError:
                p.kill(); await p.wait(); return 1,b'timeout'
            return p.returncode,out or err
        code,_=await cmd('info','--format','{{.ServerVersion}}')
        if code: return {'docker':False,'image':False,'reason':'Docker daemon 未启动或无访问权限'}
        code,_=await cmd('image','inspect',self.s.test_image,'--format','{{.Id}}')
        return {'docker':True,'image':code==0,'reason':'' if code==0 else '请先构建或准备已信任的测试镜像'}

    def command(self,root: Path,profile: str,name: str) -> list[str]:
        if profile not in PROFILES: raise ValueError('未知测试 profile')
        root=root.resolve(strict=True)
        if ',' in str(root): raise ValueError('Docker bind-mount 路径不能包含逗号，请移动应用数据目录')
        return ['docker','run','--rm','--pull=never','--name',name,
            '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges:true',
            '--pids-limit=128','--cpus=1','--memory=512m','--memory-swap=512m',
            '--user=65534:65534','--ulimit','nofile=512:512',
            '--mount',f'type=bind,src={root},dst=/input,readonly',
            '--tmpfs','/work:rw,nosuid,nodev,size=128m,mode=1777',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--workdir','/work','--env','HOME=/tmp','--env','PYTHONDONTWRITEBYTECODE=1',
            '--env','PYTHONPATH=/work','--env','CI=true',
            '--entrypoint','python',self.s.test_image,'/opt/repopilot_runner.py',profile]

    async def run(self,root: Path,profile: str,emit: Emit=noop) -> dict:
        if not self.s.tests_enabled: raise ValueError('请在设置中开启 Docker 测试，并确认本次执行')
        status=await self.availability()
        if not status['docker'] or not status['image']: raise ValueError(status['reason'])
        name='repopilot-test-'+uid()[:16]; command=self.command(root,profile,name)
        started=time.monotonic(); output=[]; size=0; timed_out=False; started_test=False
        await emit('stage',{'node':'docker','status':'running','message':'在无网络容器中运行 '+profile})
        process=await asyncio.create_subprocess_exec(*command,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
        async def consume():
            nonlocal size,started_test
            while True:
                chunk=await process.stdout.read(4096)
                if not chunk: break
                size+=len(chunk)
                if size>2_000_000: raise ValueError('测试输出超过 2 MB，已终止容器')
                text=redact(chunk.decode('utf-8',errors='replace'))
                if '__REPOPILOT_TEST_START__' in text: started_test=True
                output.append(text)
                await emit('test_log',{'text':text})
            return await process.wait()
        try:
            exit_code=await asyncio.wait_for(consume(),self.s.test_timeout)
        except asyncio.TimeoutError:
            timed_out=True; exit_code=None
        finally:
            # Killing the client alone is insufficient; explicitly remove the container.
            try:
                cleanup=await asyncio.create_subprocess_exec('docker','rm','-f',name,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(cleanup.wait(),10)
            except (OSError,asyncio.TimeoutError): pass
            if process.returncode is None:
                process.kill()
                with contextlib.suppress(Exception): await process.wait()
        return {'kind':'docker','profile':profile,'image':self.s.test_image,'executed_code':started_test,
                'status':'timeout' if timed_out else ('passed' if exit_code==0 and started_test else ('failed' if started_test else 'infrastructure_error')),
                'exit_code':exit_code,'duration_ms':round((time.monotonic()-started)*1000),
                'output':''.join(output)[-150000:],
                'notice':'记录的是固定测试进程的退出结果，不证明测试充分性，也不保证不可信代码绝对安全。'}
