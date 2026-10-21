"""Offline behavior tests; temporary databases only, no mailbox/model calls."""
import datetime as dt
from email.message import EmailMessage
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from backend.mail import core
from backend.mail.steps import project
from backend.storage import repository as repo
from backend.jobs import local_gate as gate


class LocalTasks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=core.open_db(Path(self.temp.name)/'test.sqlite3')
        message=EmailMessage();message['Subject']='测试公司笔试邀请';message['From']='test@example.invalid'
        message['Message-ID']='<local-test@example.invalid>';message.set_content('请确认参加笔试。10月24日14:00参加，23日18:00前确认。')
        core.store_message(self.db,message.as_bytes(),'INBOX','1','1',received=dt.datetime(2026,10,21,12,tzinfo=core.TZ))
        self.key=self.db.execute('SELECT id FROM mails').fetchone()[0]
        self.item={'id':self.key,'relevant':True,'requires_action':True,'company':'测试公司','role':'开发工程师',
                   'category':'笔试','next_action':'请确认参加笔试并按时参加','status':'待处理','evidence':'请确认参加笔试',
                   'event_at':'2026-10-24T14:00:00+08:00','deadline':'2026-10-23T18:00:00+08:00','important':True,'notes':'原备注'}
        core.ingest(self.db,[self.item]);repo.initialize(self.db)

    def tearDown(self):self.db.close();self.temp.cleanup()

    def state(self):return repo.state(self.db)

    def edit(self,kind,item,**value):repo.mutate(self.db,kind,{'id':item['id'],'version':item['version'],**value})

    def test_confirmation_and_attendance_are_independent(self):
        state=self.state();self.assertEqual(2,len(state['steps']))
        confirmation=next(x for x in state['steps'] if x['label']=='预约/确认参加')
        self.edit('step',confirmation,done=True)
        state=self.state();self.assertFalse(state['emails'][0]['done']);self.assertEqual(1,sum(x['done'] for x in state['steps']))
        remaining=next(x for x in state['steps'] if not x['done']);self.edit('step',remaining,done=True)
        self.assertTrue(self.state()['emails'][0]['done'])

    def create_manual(self,**overrides):
        import uuid
        value={'op':'create','id':str(uuid.uuid4()),'title':'手动测试日程','start':'2026-10-24T14:00:00+08:00','deadline':'2026-10-24T16:00:00+08:00','notes':'备注'}
        value.update(overrides);repo.edit_manual(self.db,value);return value

    def test_manual_lifecycle_preserves_every_mail_table(self):
        names=['mails','records','local_steps','local_overrides','mail_index']
        before={name:[tuple(row) for row in self.db.execute('SELECT * FROM '+name)] for name in names}
        request=self.create_manual();repo.edit_manual(self.db,request)
        self.assertEqual(1,len(self.state()['manual_events']))
        item=self.state()['manual_events'][0]
        repo.edit_manual(self.db,{**item,'op':'update','title':'已编辑标题','notes':'新备注'})
        item=self.state()['manual_events'][0]
        repo.edit_manual(self.db,{**item,'op':'complete','done':True})
        item=self.state()['manual_events'][0];self.assertTrue(item['done']);self.assertEqual('新备注',item['notes'])
        self.assertEqual(before,{name:[tuple(row) for row in self.db.execute('SELECT * FROM '+name)] for name in names})
        repo.sync_local(self.db);self.assertTrue(self.state()['manual_events'][0]['done'])
        # Local mail sync may acknowledge hashes, but manual edits alone never touch mail tables.
        before={name:[tuple(row) for row in self.db.execute('SELECT * FROM '+name)] for name in names}
        repo.edit_manual(self.db,{**item,'op':'delete'})
        self.assertFalse(self.state()['manual_events'])
        repo.edit_manual(self.db,{**item,'op':'restore','version':item['version']+1})
        item=self.state()['manual_events'][0];self.assertTrue(item['done'])
        repo.edit_manual(self.db,{**item,'op':'complete','done':False})
        after={name:[tuple(row) for row in self.db.execute('SELECT * FROM '+name)] for name in names}
        self.assertEqual(before,after);self.assertFalse(self.state()['manual_events'][0]['done'])
        self.db.close();self.db=core.open_db(Path(self.temp.name)/'test.sqlite3');repo.initialize(self.db)
        self.assertEqual('已编辑标题',self.state()['manual_events'][0]['title'])

    def test_manual_invalid_time_and_stale_edit_rejected(self):
        for values in [{'start':None,'deadline':None},{'start':'2026-10-24T14:00:00'},{'title':''}]:
            with self.assertRaises(ValueError):self.create_manual(**values)
        self.create_manual();item=self.state()['manual_events'][0]
        repo.edit_manual(self.db,{**item,'op':'complete','done':True})
        with self.assertRaises(ValueError):repo.edit_manual(self.db,{**item,'op':'delete'})

    def test_manual_confirmation_deadline_may_precede_start(self):
        self.create_manual(deadline='2026-10-23T12:00:00+08:00')
        self.assertEqual('2026-10-23T12:00:00+08:00',self.state()['manual_events'][0]['deadline'])

    def test_email_check_and_uncheck_control_all_steps(self):
        self.edit('email',self.state()['emails'][0],done=True)
        self.assertTrue(all(x['done'] for x in self.state()['steps']))
        self.edit('email',self.state()['emails'][0],done=False)
        self.assertFalse(any(x['done'] for x in self.state()['steps']))

    def test_uncheck_one_step_restores_aggregate(self):
        self.edit('email',self.state()['emails'][0],done=True)
        self.edit('step',self.state()['steps'][0],done=False)
        self.assertFalse(self.state()['emails'][0]['done'])

    def test_duplicate_sync_preserves_notes_completion_and_versions(self):
        self.edit('notes',self.state()['emails'][0],notes='手工备注，禁止覆盖')
        self.edit('step',self.state()['steps'][0],done=True)
        before=self.state()
        for _ in range(3):repo.sync_local(self.db)
        after=self.state();self.assertEqual(before['steps'],after['steps']);self.assertEqual(before['emails'],after['emails'])

    def test_stale_request_never_overwrites_newer_edit(self):
        old=self.state()['emails'][0];self.edit('notes',old,notes='新备注')
        with self.assertRaises(ValueError):self.edit('email',old,done=True)
        self.assertEqual('新备注',self.state()['emails'][0]['notes']);self.assertFalse(self.state()['emails'][0]['done'])

    def test_time_nodes_and_estimated_deadline(self):
        for event,deadline,count in [(self.item['event_at'],None,2),(None,self.item['deadline'],2),(None,None,2)]:
            item={**self.item,'event_at':event,'deadline':deadline,'received':'2026-10-21T12:00:00+08:00','deadline_estimated':True}
            nodes=project([item],repo.values);self.assertEqual(count,len(nodes))
            for node in nodes.values():
                self.assertEqual(node['节点时间'],node['结束时间'])
                if node['节点时间']==deadline and deadline:self.assertIn('估算',node['时间依据'])

    def test_without_confirmation_only_one_timed_node(self):
        item={**self.item,'next_action':'参加笔试','evidence':'笔试安排','received':'2026-10-21T12:00:00+08:00','deadline':None}
        self.assertEqual(1,len(project([item],repo.values)))
        item.update(event_at=None,deadline=self.item['deadline'])
        self.assertEqual(1,len(project([item],repo.values)))

    def test_local_filter_prioritizes_explicit_action(self):
        for title in ['验证码','投递成功','岗位推荐']:
            self.assertIsNone(gate.skip_reason({'subject':title,'body':'请您完成在线笔试，并确认参加面试'}))
        self.assertEqual('receipt',gate.skip_reason({'subject':'投递成功','body':'感谢申请'}))

    def test_no_new_candidates_means_no_model_call(self):
        reviewer=unittest.mock.Mock(side_effect=AssertionError('不应调用模型'))
        with patch.object(gate,'RUNTIME',Path(self.temp.name)):
            result=gate.run_once(self.db,{'model_provider':'codebuddy_cn'},scanner=lambda *_:{'new':0},reviewer=reviewer,credentials_loader=lambda:{})
        reviewer.assert_not_called();self.assertEqual(0,result['model_calls']);self.assertEqual(2,len(self.state()['steps']))

    def test_provider_cannot_fall_back_to_another_cli(self):
        for provider in ['codex','qoder_cn',None]:
            with self.assertRaises(RuntimeError):gate.run_once(self.db,{'model_provider':provider})

    def test_reopen_database_persists_changes(self):
        self.edit('email',self.state()['emails'][0],done=True)
        self.db.close();self.db=core.open_db(Path(self.temp.name)/'test.sqlite3');repo.initialize(self.db)
        self.assertTrue(self.state()['emails'][0]['done'])

    def envelope(self,text):
        return subprocess.CompletedProcess([],0,stdout=json.dumps([
            {'type':'message','role':'assistant','providerData':{'model':'deepseek-v4.1-flash'}},
            {'type':'result','subtype':'success','is_error':False,'result':text}]),stderr='')

    def test_corrected_output_is_audited_and_committed_only_once(self):
        self.db.execute('UPDATE mails SET reviewed=0');self.db.commit()
        decision={k:self.item.get(k) for k in gate.response_schema()['properties']['decisions']['items']['required']}
        decision.update(resolves=[],event_options=[])
        valid=json.dumps({'decisions':[decision]},ensure_ascii=False)
        with patch.object(gate,'RUNTIME',Path(self.temp.name)),patch.object(gate.subprocess,'run',side_effect=[self.envelope('{bad json'),self.envelope(valid)]) as cli:
            result=gate.run_once(self.db,{'model_provider':'codebuddy_cn','codebuddy_cli':'fake-cli','codebuddy_model':'deepseek-v4.1-flash'},scanner=lambda *_:{'new':0},credentials_loader=lambda:{})
        self.assertEqual(2,cli.call_count);self.assertEqual(2,result['model_calls'])
        manifest=json.loads(next(Path(self.temp.name).glob('audits/*/manifest.json')).read_text(encoding='utf-8'))
        self.assertEqual('validated',manifest['correction']['status']);self.assertEqual('deepseek-v4.1-flash',manifest['actual_model'])
        self.assertEqual('verified',manifest['sync_status']);self.assertEqual('local_sqlite',manifest['sync_attempts'][-1]['mail_results'][0]['storage'])
        self.assertEqual(1,self.db.execute('SELECT count(*) FROM records').fetchone()[0]);self.assertEqual(2,len(self.state()['steps']))

    def test_failed_correction_preserves_candidate_and_stops(self):
        self.db.execute('UPDATE mails SET reviewed=0');self.db.commit()
        with patch.object(gate,'RUNTIME',Path(self.temp.name)),patch.object(gate.subprocess,'run',return_value=self.envelope('invalid JSON')) as cli:
            with self.assertRaises(RuntimeError):
                gate.run_once(self.db,{'model_provider':'codebuddy_cn','codebuddy_cli':'fake-cli'},scanner=lambda *_:{'new':0},credentials_loader=lambda:{})
        self.assertEqual(2,cli.call_count);self.assertEqual(0,self.db.execute('SELECT reviewed FROM mails').fetchone()[0])
        manifest=json.loads(next(Path(self.temp.name).glob('audits/*/manifest.json')).read_text(encoding='utf-8'))
        self.assertEqual('validation_failed',manifest['correction']['status']);self.assertEqual('not_started',manifest['ingest_status'])


if __name__=='__main__':unittest.main()
