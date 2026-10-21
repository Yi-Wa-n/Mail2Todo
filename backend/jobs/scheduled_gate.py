"""Windowless Windows task entry, independent of the web service lifetime."""
from contextlib import redirect_stdout,redirect_stderr
import json
import sys
from backend.storage.vault import HOME
from backend.mail.core import now

def main():
    with (HOME/'local-gate.log').open('a',encoding='utf-8') as log,redirect_stdout(log),redirect_stderr(log):
        print(json.dumps({'scheduled_start':True,'at':now().isoformat(),'local_only':True}),flush=True)
        try:
            from backend.jobs.manual_scan import main as scan
            scan()
        except Exception as error:
            print(json.dumps({'scheduled_failed':True,'at':now().isoformat(),'error_type':type(error).__name__}),flush=True)
            return 1
    return 0

if __name__=='__main__':sys.exit(main())
