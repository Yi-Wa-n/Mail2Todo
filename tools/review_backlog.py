"""Explicit recovery of already-scanned mail; never connects to the mailbox."""
import datetime as dt
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from backend.cli.manager import locked
    from backend.storage.vault import HOME,account_home
    from backend.storage import repository as repo
    from backend.mail.core import get_meta,set_meta
    from backend.jobs import local_gate as gate
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    with locked():
        home=account_home()
        gate.RUNTIME=home/'local-gate'
        db=repo.connect()
        try:
            before=repo.state(db)
            previous_success=get_meta(db,'local_gate_last_success')
            previous_result=get_meta(db,'local_gate_last_result',{})
            # SQLite backup includes the committed WAL state, without interrupting the UI.
            import sqlite3
            folder=home/'backups'/'routing-recovery';folder.mkdir(parents=True,exist_ok=True)
            backup=sqlite3.connect(folder/(stamp+'.sqlite3'))
            try: db.backup(backup)
            finally: backup.close()
            result=gate.run_once(db,json.loads((HOME/'local-gate.json').read_text(encoding='utf-8')),
                scanner=lambda *_:{'new_messages':0,'recruitment_candidates':0,'stored_mail_recovery':True},
                credentials_loader=lambda:{})
            set_meta(db,'routing_recovery_last_result',result)
            # Reviewing stored messages is not a new successful IMAP scan.
            set_meta(db,'local_gate_last_success',previous_success)
            set_meta(db,'local_gate_last_result',previous_result)
            after=repo.state(db)
            old_mail={m['id']:m for m in before['emails']}
            new_mail={m['id']:m for m in after['emails']}
            notes_ok=all(new_mail[key]['notes']==m['notes'] and new_mail[key]['done']==m['done'] for key,m in old_mail.items())
            old_steps={s['id']:s['done'] for s in before['steps']}
            steps_ok=all(old_steps.get(s['id'],s['done'])==s['done'] for s in after['steps'])
            manual_ok=before['manual_events']==after['manual_events']
            if not (notes_ok and steps_ok and manual_ok):raise RuntimeError('Manual state preservation check failed')
            report={**result,'previous_notes_and_completion_preserved':notes_ok,
                    'previous_steps_preserved':steps_ok,'manual_events_preserved':manual_ok,
                    'new_actionable_emails':len(set(new_mail)-set(old_mail)),
                    'new_steps':len(after['steps'])-len(before['steps']),
                    'pending_review':db.execute('SELECT count(*) FROM mails WHERE reviewed=0').fetchone()[0]}
            (folder/(stamp+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            set_meta(db,'local_last_error',None)
            print(json.dumps(report,ensure_ascii=False),flush=True)
        finally:db.close()
    from backend.storage.cloud_sync import sync_once
    synced=sync_once(force=True)
    print(json.dumps({'cloud_sync':synced},ensure_ascii=False),flush=True)
    return 0 if synced.get('ok') else 1


if __name__=='__main__':sys.exit(main())
