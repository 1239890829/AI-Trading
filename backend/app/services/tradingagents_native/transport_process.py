"""Existing Codex CLI with tools disabled; one fixed-input baseline only."""
import hashlib
import os
import signal
import subprocess

DISABLE = ['shell_tool', 'shell_snapshot', 'multi_agent', 'apps', 'plugins', 'remote_plugin',
           'hooks', 'goals', 'memories', 'image_generation', 'skill_search',
           'skill_mcp_dependency_install', 'code_mode_host', 'view_image']

class TransportError(RuntimeError):
    def __init__(self, diagnostic):
        self.diagnostic=diagnostic
        super().__init__(diagnostic['kind'])

def launch(args, prompt, *, env, cwd, timeout=360):
    proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,env=env,cwd=cwd,start_new_session=True)
    try:
        out,err=proc.communicate(prompt,timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid,signal.SIGTERM)
        try:
            out,err=proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL)
            out,err=proc.communicate()
        raise TransportError({'kind':'timeout','usage':None,
            'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),
            'stderr_sha256':hashlib.sha256(err.encode()).hexdigest()}) from None
    return out,err,proc.returncode
