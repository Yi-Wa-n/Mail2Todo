"""Explicit local-only scan. No document APIs or mail mutations."""
import json
from backend.cli.manager import locked
from backend.storage.repository import connect,initialize
from backend.storage.vault import HOME
from backend.jobs.local_gate import run_once
from backend.mail.core import now,set_meta


def main():
    with locked():
        from backend.storage.vault import account_home
        import backend.jobs.local_gate as gate
        gate.RUNTIME=account_home()/'local-gate'
        db=connect()
        try:
            initialize(db)
            config=json.loads((HOME/'local-gate.json').read_text(encoding='utf-8'))
            result=run_once(db,config)
            set_meta(db,'local_last_error',None)
            with (HOME/'local-gate.log').open('a',encoding='utf-8') as log:log.write(json.dumps(result,ensure_ascii=False)+'\n')
        except Exception as error:
            entry={'at':now().isoformat(),'status':'failed','error_type':type(error).__name__}
            set_meta(db,'local_last_error',entry)
            with (HOME/'local-gate.log').open('a',encoding='utf-8') as log:log.write(json.dumps(entry,ensure_ascii=False)+'\n')
            raise
        finally:
            db.close()
            # A failed upload never repeats mail classification or changes scan success.
            from backend.storage.cloud_sync import sync_once
            try:sync_once()
            except Exception:pass


if __name__=='__main__':main()
