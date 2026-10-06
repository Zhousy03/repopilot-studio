"""Trusted container entrypoint. Arguments map to fixed commands, not a shell."""
import os
import shutil
import sys
from pathlib import Path

profiles={
    'pytest':['python','-m','pytest','-q','-p','no:cacheprovider'],
    'unittest':['python','-m','unittest','discover','-s','tests','-v'],
    'node-test':['node','--test'],
}
if len(sys.argv)!=2 or sys.argv[1] not in profiles:
    raise SystemExit('Unknown profile')
for p in Path('/input').iterdir():
    if p.is_symlink(): raise SystemExit('Source symlink is not allowed')
    target=Path('/work')/p.name
    if p.is_dir(): shutil.copytree(p,target,symlinks=False)
    else: shutil.copyfile(p,target)
os.chdir('/work')
print('__REPOPILOT_TEST_START__',flush=True)
os.execvp(profiles[sys.argv[1]][0],profiles[sys.argv[1]])
