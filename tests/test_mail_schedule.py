"""Mail schedule revisions against temporary databases; no mailbox/model calls."""
import datetime as dt
import json
from email.message import EmailMessage
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.mail import core
from backend.storage import repository as repo


class MailScheduleTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/'test.sqlite3'
        self.db=core.open_db(self.path)
        self.item={'relevant':True,'requires_action':True,'company':'测试公司','role':'开发',
                   'category':'面试','next_action':'参加面试','status':'待处理','evidence':'面试安排',
                   'event_at':'2026-10-24T14:00:00+08:00','deadline':None,'important':True,'notes':'原备注'}
        self.key=self.add_mail()
        repo.initialize(self.db)

    def tearDown(self):self.db.close();self.folder.cleanup()

    def add_mail(self,previous=None,**changes):
        index=self.db.execute('SELECT count(*) FROM mails').fetchone()[0]+1
        msg=EmailMessage();msg['Subject']='测试通知';msg['From']='test@example.invalid'
        msg['Message-ID']=f'<schedule-{index}@example.invalid>';msg.set_content('参加面试')
        core.store_message(self.db,msg.as_bytes(),'INBOX','1',str(index),received=dt.datetime(2026,10,21,12,index,tzinfo=core.TZ))
        keys={row[0] for row in self.db.execute('SELECT mail_id FROM records')}
        key=next(row[0] for row in self.db.execute('SELECT id FROM mails') if row[0] not in keys)
        core.ingest(self.db,[{**self.item,'id':key,'supersedes':previous,**changes}])
        return key

    def mail(self,key=None):return next(m for m in repo.state(self.db)['emails'] if m['id']==(key or self.key))

    def edit(self,key=None,op='save',**changes):
        mail=self.mail(key)
        request={name:mail[name] for name in ('category','start','deadline','action')}
        repo.edit_mail_schedule(self.db,dict(request,id=mail['id'],version=mail['version'],op=op,**changes))

    def test_edit_preserves_original_and_completion_and_reopens(self):
        mail=self.mail();repo.mutate(self.db,'email',{'id':self.key,'version':mail['version'],'done':True})
        self.edit(category='笔试',start='2026-10-25T16:00:00+08:00',deadline='2026-10-23T12:00:00+08:00',action='提前准备材料并参加笔试')
        mail=self.mail();self.assertEqual('面试',mail['original']['category']);self.assertEqual('笔试',mail['category'])
        self.assertEqual(self.item['event_at'],mail['original']['start']);self.assertEqual('原备注',mail['notes'])
        self.assertEqual(4,len(mail['manual_fields']));self.assertEqual(1,len(mail['edit_history']))
        steps=[s for s in repo.state(self.db)['steps'] if s['active']]
        attendance=next(s for s in steps if s['label']=='参加笔试')
        self.assertTrue(attendance['done']);self.assertEqual(mail['action'],attendance['action'])
        payload=json.loads(self.db.execute('SELECT payload FROM records WHERE mail_id=?',(self.key,)).fetchone()[0])
        self.assertEqual('面试',payload['category']);self.assertEqual(self.item['event_at'],payload['event_at'])
        self.db.close();self.db=core.open_db(self.path);repo.initialize(self.db)
        self.assertEqual(mail,self.mail())

    def test_new_notice_inherits_only_edited_fields_and_keeps_one_schedule(self):
        self.edit(start='2026-10-24T15:00:00+08:00',action='自定待办')
        key=self.add_mail(self.key,category='AI面试',event_at='2026-10-24T16:00:00+08:00',deadline='2026-10-23T18:00:00+08:00',next_action='新的待办')
        repo.sync_local(self.db);mail=self.mail(key)
        self.assertEqual('AI面试',mail['category']);self.assertEqual('2026-10-24T15:00:00+08:00',mail['start'])
        self.assertEqual('自定待办',mail['action']);self.assertEqual('2026-10-23T18:00:00+08:00',mail['deadline'])
        self.assertEqual({'start','action'},set(mail['pending_manual_review']))
        self.assertEqual('2026-10-24T16:00:00+08:00',mail['original']['start'])
        self.assertEqual(['save','inherited'],[entry['kind'] for entry in mail['edit_history']])
        self.assertFalse(self.mail()['active'])
        state=repo.state(self.db);self.assertEqual({key},{s['mail_id'] for s in state['steps'] if s['active']})
        self.assertEqual(1,sum(s['label']=='参加AI面试' and s['active'] for s in state['steps']))
        with self.assertRaises(ValueError):self.edit(start='2026-10-24T20:00:00+08:00')
        self.edit(key);self.assertFalse(self.mail(key)['pending_manual_review'])
        self.assertEqual([],self.mail(key)['edit_history'][-1]['fields'])

    def test_adopt_notification_is_explicit_and_retains_history_across_updates(self):
        self.edit(start='2026-10-24T15:00:00+08:00')
        key=self.add_mail(self.key,event_at='2026-10-24T16:00:00+08:00');repo.sync_local(self.db)
        self.edit(key,op='use-notification');mail=self.mail(key)
        self.assertEqual('2026-10-24T16:00:00+08:00',mail['start']);self.assertFalse(mail['manual_fields'])
        self.assertEqual('use-notification',mail['edit_history'][-1]['kind'])
        latest=self.add_mail(key,event_at='2026-10-24T17:00:00+08:00');repo.sync_local(self.db)
        self.assertEqual('2026-10-24T17:00:00+08:00',self.mail(latest)['start'])
        self.assertFalse(self.mail(latest)['manual_fields']);self.assertEqual(3,len(self.mail(latest)['edit_history']))

    def test_same_mail_reingest_and_repeated_sync_cannot_erase_corrections(self):
        self.edit(start='2026-10-24T15:00:00+08:00')
        stale=self.mail();core.ingest(self.db,[dict(self.item,id=self.key,event_at='2026-10-24T18:00:00+08:00')]);repo.sync_local(self.db)
        mail=self.mail();self.assertEqual('2026-10-24T15:00:00+08:00',mail['start'])
        self.assertEqual(self.item['event_at'],mail['original']['start']);self.assertEqual(['start'],mail['pending_manual_review'])
        self.assertGreater(mail['version'],stale['version'])
        for _ in range(3):repo.sync_local(self.db)
        self.assertEqual(mail,self.mail())
        with self.assertRaises(ValueError):repo.edit_mail_schedule(self.db,dict(stale,op='save'))

    def test_invalid_and_stale_edits_are_atomic(self):
        before=self.mail()
        for change in [{'category':'invalid'},{'action':''},{'start':'2026-10-24T14:00:00'},{'start':'2026-02-30T14:00:00+08:00'}]:
            with self.assertRaises(ValueError):self.edit(**change)
            self.assertEqual(before,self.mail())
        with patch.object(repo,'project',side_effect=RuntimeError('projection failed')):
            with self.assertRaises(RuntimeError):self.edit(action='必须回滚')
        self.assertEqual(before,self.mail())
        self.edit(action='一次修订')
        with self.assertRaises(ValueError):repo.edit_mail_schedule(self.db,dict(before,op='save',action='过期修订'))
        self.assertEqual('一次修订',self.mail()['action'])

    def test_clearing_unknown_times_is_protected_and_retains_original(self):
        self.edit(start=None)
        key=self.add_mail(self.key,event_at='2026-10-24T17:00:00+08:00');repo.sync_local(self.db)
        self.assertIsNone(self.mail(key)['start']);self.assertEqual(['start'],self.mail(key)['pending_manual_review'])
        self.assertFalse(any(s['active'] and s['at'] for s in repo.state(self.db)['steps']))

    def test_legacy_overrides_are_preserved_and_imported_once(self):
        payload=json.loads(self.db.execute('SELECT payload FROM records WHERE mail_id=?',(self.key,)).fetchone()[0])
        for name in list(payload):
            if name.startswith('manual_schedule_'):payload.pop(name)
        payload['manual_values']={'开始时间':'2026-10-24T20:00:00+08:00'}
        with self.db:self.db.execute('UPDATE records SET payload=? WHERE mail_id=?',(core.canonical(payload),self.key))
        repo.sync_local(self.db);mail=self.mail()
        self.assertEqual('2026-10-24T20:00:00+08:00',mail['start'])
        self.assertEqual('imported',mail['edit_history'][0]['kind'])
        repo.sync_local(self.db);self.assertEqual(mail,self.mail())

    def test_multiple_notifications_keep_latest_revision_and_all_history(self):
        self.edit(start='2026-10-24T15:00:00+08:00')
        second=self.add_mail(self.key,event_at='2026-10-24T16:00:00+08:00');repo.sync_local(self.db)
        self.edit(second,start='2026-10-24T18:00:00+08:00')
        third=self.add_mail(second,event_at='2026-10-24T17:00:00+08:00');repo.sync_local(self.db)
        mail=self.mail(third)
        self.assertEqual('2026-10-24T18:00:00+08:00',mail['start'])
        self.assertEqual(4,len(mail['edit_history']));self.assertEqual(['start'],mail['pending_manual_review'])
        self.assertEqual({third},{s['mail_id'] for s in repo.state(self.db)['steps'] if s['active']})

    def test_step_versions_change_when_projected_schedule_changes(self):
        before=next(s for s in repo.state(self.db)['steps'] if s['active'])
        self.edit(start='2026-10-24T15:00:00+08:00')
        after=next(s for s in repo.state(self.db)['steps'] if s['id']==before['id'])
        self.assertGreater(after['version'],before['version'])
        with self.assertRaises(ValueError):repo.mutate(self.db,'step',dict(before,done=True))
