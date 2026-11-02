"""Real Store regressions for native digest custody and delivery admission."""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('Unix integration service; run on WSL/Linux')
import json
import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'host'))
import integrations_service as service
import integrations_app_service as app_service
import integrations_apps as apps
from integrations_common import Store, IntegrationError

class DigestSecurity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.svc=service.Integrations(self.store)
        cfg=self.store.config();cfg['notifications'].update(enabled=True,scheduledTasks='all')
        cfg['apps']['news'].update(enabled=True,url='http://127.0.0.1:11029',username='owner')
        cfg['apps']['news']['digest']['enabled']=True
        self.svc.save(cfg['revision'],{key:cfg[key] for key in ('notifications','notes','mcp','apps')},{'apps':{'news':'private-test-key'}})
        self.items=[{'id':'story-1','title':'Actual first headline','feed':'Local','link':'https://example.invalid/1','summary':''},
                    {'id':'story-2','title':'Actual second headline','feed':'Local','link':'https://example.invalid/2','summary':''}]
        self.sent=[]
        self.read_patch=patch.object(apps,'unread_news',side_effect=lambda *_a,**_kw:list(self.items));self.read_patch.start();self.addCleanup(self.read_patch.stop)
        self.send_patch=patch.object(app_service,'send_notification',side_effect=lambda _c,_s,title,message,*_a,**_kw:self.sent.append((title,message)));self.send_patch.start();self.addCleanup(self.send_patch.stop)
        self.plan=self.svc.digest_control('digest-plan',{})['plan']
        self.svc.digest_control('digest-job-record',{'nonce':self.plan['management']['nonce'],'jobId':'native-digest'})
        self.serial=0
    def finish(self,**changes):
        self.serial+=1
        event={'jobId':'native-digest','description':self.plan['description'],'name':'Daily news digest','status':'error','runAtMs':self.serial,'error':'model unavailable'}
        event.update(changes)
        return self.svc.task_finished(self.store.config(),event)
    def context(self,n='one'):
        return {'sessionId':n,'sessionKey':'agent:pixel:cron:native-digest:run:'+n}
    def read(self,context):
        cfg=self.store.config();return self.svc.remember_digest_read(context,list(self.items),cfg,self.store.secrets()['apps']['news'])
    def history(self):return self.store._read('digest-history.json',None)

    def test_managed_journal_survives_restart_and_rejects_stale_or_changed_identity(self):
        restarted=service.Integrations(Store(self.temp.name));plan=restarted.digest_control('digest-plan',{})['plan']
        self.assertEqual(plan['management']['jobId'],'native-digest');self.assertEqual(plan['description'],self.plan['description'])
        for request in ({'nonce':'b'*32,'jobId':'native-digest'},{'nonce':plan['management']['nonce'],'jobId':'foreign'}):
            with self.assertRaises(IntegrationError):restarted.digest_control('digest-job-clear',request)
        with self.assertRaises(IntegrationError):restarted.digest_control('digest-job-record',{'nonce':plan['management']['nonce'],'jobId':'other'})
        self.assertEqual(restarted.digest_control('digest-plan',{})['plan']['management']['jobId'],'native-digest')

    def test_crash_journal_is_durable_before_native_id_is_known(self):
        self.store._write('digest-job.json',None)
        plan=self.svc.digest_control('digest-plan',{})['plan'];self.assertIsNone(plan['management']['jobId'])
        reopened=service.Integrations(Store(self.temp.name))
        self.assertEqual(reopened.digest_control('digest-plan',{})['plan']['management'],plan['management'])
        reopened.digest_control('digest-job-record',{'nonce':plan['management']['nonce'],'jobId':'returned-by-openclaw'})
        self.assertEqual(reopened.digest_control('digest-plan',{})['plan']['management']['jobId'],'returned-by-openclaw')

    def test_exact_native_read_context_qualifies_optional_model_brief_and_factual_titles(self):
        ctx=self.context();self.read(ctx)
        self.assertTrue(self.finish(status='ok',summary='### Local\n- **A concise brief**',runAtMs=1,**ctx)['notified'])
        text=self.sent[0][1];self.assertTrue(text.startswith('Local\n- A concise brief'))
        self.assertIn('Stories included:',text)
        for item in self.items:self.assertIn(item['title'],text)
        self.assertNotIn('**',text)

    def test_other_native_context_cannot_borrow_a_read_or_model_summary(self):
        self.read(self.context('first'))
        self.assertTrue(self.finish(status='ok',summary='UNSOURCED OTHER RUN',**self.context('second'))['notified'])
        self.assertNotIn('UNSOURCED',self.sent[0][1]);self.assertIn(self.items[0]['title'],self.sent[0][1])

    def test_old_global_digest_memory_is_not_receipt_authority(self):
        self.svc.digest_items=list(self.items);self.svc.digest_called_at=time.time()+1000
        self.finish(status='ok',summary='UNSOURCED LEGACY',**self.context())
        self.assertNotIn('UNSOURCED',self.sent[0][1])

    def test_new_connection_cannot_reuse_old_context_snapshot(self):
        self.read(self.context());cfg=self.store.config();cfg['apps']['news']['username']='different-owner'
        self.svc.save(cfg['revision'],{key:cfg[key] for key in ('notifications','notes','mcp','apps')},{})
        self.items=[{**self.items[0],'id':'different-story','title':'Different account headline'}]
        self.finish(status='ok',summary='OLD ACCOUNT SUMMARY',**self.context())
        self.assertNotIn('OLD ACCOUNT',self.sent[0][1]);self.assertIn('Different account',self.sent[0][1])

    def test_overlapping_owner_and_cron_delivery_sends_each_story_only_once(self):
        entered,release=threading.Event(),threading.Event();errors=[]
        def sending(_c,_s,title,message,*_a,**_kw):
            self.sent.append((title,message));entered.set();self.assertTrue(release.wait(5))
        with patch.object(app_service,'send_notification',side_effect=sending):
            def owner():
                try:self.svc.send_digest_now()
                except Exception as e:errors.append(e)
            def cron():
                try:self.finish()
                except Exception as e:errors.append(e)
            first=threading.Thread(target=owner);first.start();self.assertTrue(entered.wait(5))
            second=threading.Thread(target=cron);second.start();release.set();first.join(5);second.join(5)
            self.assertFalse(first.is_alive() or second.is_alive());self.assertEqual(errors,[])
        self.assertEqual(len(self.sent),1)
        for item in self.items:self.assertEqual(sum(item['title'] in msg for _,msg in self.sent),1)

    def test_uncertain_delivery_is_durable_and_not_replayed_after_restart(self):
        with patch.object(app_service,'send_notification',side_effect=IntegrationError('transport','reply lost')):
            with self.assertRaises(IntegrationError):self.svc.send_digest_now()
        history=self.history();self.assertEqual({state for src in history['sources'].values() for state in src['stories'].values()},{'unknown'})
        restarted=service.Integrations(Store(self.temp.name));cfg=self.store.config()
        self.assertEqual(restarted._new_stories(cfg['apps']['news'],'private-test-key'),[])
        self.assertFalse(restarted.task_finished(cfg,{'jobId':'native-digest','description':self.plan['description'],'status':'error','runAtMs':100})['notified'])
        self.assertEqual(self.sent,[])

    def test_exact_finished_event_does_not_send_new_stories_on_replay(self):
        self.assertTrue(self.finish(runAtMs=10)['notified'])
        self.items=[{**self.items[0],'id':'new-story','title':'Later headline'}]
        self.assertFalse(self.finish(runAtMs=10)['notified']);self.assertEqual(len(self.sent),1)
        self.assertTrue(self.finish(runAtMs=11)['notified']);self.assertIn('Later headline',self.sent[-1][1])

    def test_failure_streak_and_recovery_notices_survive_service_restart(self):
        event={'jobId':'other-task','name':'Backup','status':'error','runAtMs':1,'error':'disk full'}
        self.assertTrue(self.svc.task_finished(self.store.config(),event)['notified'])
        restarted=service.Integrations(Store(self.temp.name))
        self.assertFalse(restarted.task_finished(self.store.config(),{**event,'runAtMs':2})['notified'])
        self.assertTrue(restarted.task_finished(self.store.config(),{**event,'status':'ok','runAtMs':3})['notified'])
        self.assertFalse(restarted.task_finished(self.store.config(),{**event,'runAtMs':2})['notified'])
        self.assertTrue(restarted.task_finished(self.store.config(),{**event,'runAtMs':4})['notified'])
        self.assertEqual(len(self.sent),3)

    def test_missing_native_identity_never_sends_notice(self):
        for event in ({'name':'x','status':'ok','runAtMs':1},{'jobId':'x','status':'ok','runAtMs':0},{'jobId':'x','status':'ok','runAtMs':True}):
            self.assertFalse(self.svc.task_finished(self.store.config(),event)['notified'])
        self.assertEqual(self.sent,[])

    def test_multibyte_batch_is_fully_visible_and_leftovers_remain_new(self):
        self.items=[{'id':str(n),'feed':'Feed '+str(n),'title':str(n)+' 📰'*195,'link':'https://example.invalid/'+('x'*480),'summary':''} for n in range(40)]
        selected=self.read(self.context());self.assertGreater(len(selected),0);self.assertLess(len(selected),15)
        self.assertLessEqual(len(apps.digest_text(selected)),7400)
        self.finish(status='ok',summary='A short model brief',**self.context())
        text=self.sent[0][1];self.assertLessEqual(len(text.encode()),apps.MAX_NOTICE_BYTES)
        for item in selected:self.assertIn(item['title'],text)
        remaining=self.svc._new_stories(self.store.config()['apps']['news'],'private-test-key')
        self.assertEqual([item['id'] for item in remaining],[item['id'] for item in self.items[len(selected):]])
        source=next(iter(self.history()['sources'].values()));self.assertEqual(len(source['stories']),len(selected))

    def test_settings_change_during_provider_read_refuses_delivery_without_blocking_settings_lock(self):
        def reading(*_a,**_kw):
            cfg=self.store.config();cfg['notifications']['url']='http://127.0.0.1:9999';self.store.save_config(cfg);return list(self.items)
        with patch.object(apps,'unread_news',side_effect=reading):
            with self.assertRaisesRegex(IntegrationError,'settings changed'):self.svc.send_digest_now()
        self.assertEqual(self.sent,[]);self.assertIsNone(self.history())

    def test_broken_history_fails_before_outbound_send_and_is_preserved(self):
        self.store._write('digest-history.json',{'unexpected':True});raw=(self.store.root/'digest-history.json').read_bytes()
        with self.assertRaises(IntegrationError):self.svc.send_digest_now()
        self.assertEqual((self.store.root/'digest-history.json').read_bytes(),raw);self.assertEqual(self.sent,[])

    def test_empty_digest_once_per_local_day_survives_restart_but_explicit_owner_send_is_allowed(self):
        self.items=[];self.assertTrue(self.finish()['notified']);self.assertFalse(self.finish()['notified'])
        restarted=service.Integrations(Store(self.temp.name));self.svc=restarted
        self.assertFalse(self.finish()['notified'])
        history=self.history()
        for source in history['sources'].values():source['lastAttemptDay']='2000-01-01'
        self.store._write('digest-history.json',history)
        self.assertTrue(self.finish()['notified']);self.assertFalse(self.finish()['notified'])
        self.assertIn('Digest sent:',self.svc.send_digest_now());self.assertEqual(len(self.sent),3)

    def test_private_tokens_and_story_titles_are_not_stored_in_delivery_ledger(self):
        self.svc.send_digest_now();raw=(self.store.root/'digest-history.json').read_text()
        self.assertNotIn('private-test-key',raw)
        for item in self.items:self.assertNotIn(item['title'],raw)

    def uncertain(self):
        with patch.object(app_service,'send_notification',side_effect=IntegrationError('notification-failed','reply lost')):
            with self.assertRaises(IntegrationError):self.svc.send_digest_now()
        card=self.svc.digest_recovery()[0]
        self.assertTrue(card['canRetry']);return card

    def test_explicit_owner_retry_uses_exact_stored_message_not_new_provider_data(self):
        card=self.uncertain();self.items=[{**self.items[0],'title':'NEW UNREQUESTED STORY','id':'later'}]
        with patch.object(apps,'unread_news',side_effect=AssertionError('retry must not read provider')):
            self.svc.retry_digest({'attemptId':card['attemptId'],'expectedRevision':self.store.config()['revision']})
        self.assertEqual(self.sent[-1][1],card['messagePreview']);self.assertNotIn('NEW UNREQUESTED',self.sent[-1][1])
        self.assertEqual(self.svc.digest_recovery(),[])
        self.assertNotIn('private-test-key',(self.store.root/'digest-attempts.json').read_text())
        self.assertEqual((self.store.root/'digest-attempts.json').stat().st_mode&0o777,0o600)

    def test_retry_requires_current_revision_and_uses_explicit_current_destination(self):
        card=self.uncertain();old=self.store.config()['revision'];cfg=self.store.config();cfg['revision']+=1
        cfg['notifications']['url']='http://127.0.0.1:9999';self.store.save_config(cfg)
        with self.assertRaises(IntegrationError):self.svc.retry_digest({'attemptId':card['attemptId'],'expectedRevision':old})
        self.assertEqual(self.sent,[]);self.assertIn(':9999',self.svc.digest_recovery()[0]['notificationDestination'])
        destinations=[]
        with patch.object(app_service,'send_notification',side_effect=lambda cfg,*_a,**_kw:destinations.append(cfg['notifications']['url'])):
            self.svc.retry_digest({'attemptId':card['attemptId'],'expectedRevision':cfg['revision']})
        self.assertEqual(destinations,['http://127.0.0.1:9999'])

    def test_retry_cannot_cross_news_connection_or_run_as_agent(self):
        card=self.uncertain();cfg=self.store.config();cfg['apps']['news']['username']='another-owner';self.store.save_config(cfg)
        self.assertFalse(self.svc.digest_recovery()[0]['canRetry'])
        with self.assertRaises(IntegrationError):self.svc.retry_digest({'attemptId':card['attemptId'],'expectedRevision':cfg['revision']})
        with self.assertRaises(IntegrationError):self.svc.handle('agent',{'schemaVersion':1,'action':'retry-digest','attemptId':card['attemptId'],'expectedRevision':cfg['revision']})
        self.assertEqual(self.sent,[])

    def test_two_owner_clicks_consume_the_same_recovery_once(self):
        card=self.uncertain();request={'attemptId':card['attemptId'],'expectedRevision':self.store.config()['revision']};outcomes=[]
        def retry():
            try:self.svc.retry_digest(request);outcomes.append('sent')
            except IntegrationError:outcomes.append('refused')
        a,b=threading.Thread(target=retry),threading.Thread(target=retry);a.start();b.start();a.join(5);b.join(5)
        self.assertFalse(a.is_alive()or b.is_alive());self.assertCountEqual(outcomes,['sent','refused']);self.assertEqual(len(self.sent),1)

    def test_another_unknown_retry_requires_a_new_explicit_owner_decision(self):
        card=self.uncertain()
        with patch.object(app_service,'send_notification',side_effect=IntegrationError('notification-failed','reply lost again')):
            with self.assertRaises(IntegrationError):self.svc.retry_digest({'attemptId':card['attemptId'],'expectedRevision':self.store.config()['revision']})
        new=self.svc.digest_recovery()[0];self.assertNotEqual(new['attemptId'],card['attemptId']);self.assertEqual(new['messagePreview'],card['messagePreview'])
        with self.assertRaises(IntegrationError):self.svc.retry_digest({'attemptId':card['attemptId'],'expectedRevision':self.store.config()['revision']})
        self.assertEqual(self.sent,[])

    def test_owner_view_does_not_block_or_offer_retry_while_delivery_is_active(self):
        entered,release=threading.Event(),threading.Event();failures=[]
        def send(*_a,**_kw):entered.set();self.assertTrue(release.wait(5))
        def run():
            try:self.svc.send_digest_now()
            except Exception as error:failures.append(error)
        with patch.object(app_service,'send_notification',side_effect=send):
            worker=threading.Thread(target=run);worker.start();self.assertTrue(entered.wait(5))
            cards=self.svc.digest_recovery();self.assertEqual(len(cards),1);self.assertFalse(cards[0]['canRetry']);self.assertIn('still running',cards[0]['reason'])
            release.set();worker.join(5);self.assertFalse(worker.is_alive());self.assertEqual(failures,[])

    def test_crash_between_attempt_and_history_is_recoverable_and_not_automatically_replayed(self):
        write=self.store._write
        def fail_history(name,value):
            if name=='digest-history.json':raise OSError('disk sync failed')
            return write(name,value)
        with patch.object(self.store,'_write',side_effect=fail_history):
            with self.assertRaises(OSError):self.svc.send_digest_now()
        self.assertEqual(self.sent,[]);restarted=service.Integrations(Store(self.temp.name));cfg=self.store.config()
        self.assertEqual(restarted._new_stories(cfg['apps']['news'],'private-test-key'),[])
        card=restarted.digest_recovery()[0];self.assertTrue(card['canRetry'])
        restarted.retry_digest({'attemptId':card['attemptId'],'expectedRevision':cfg['revision']})
        self.assertEqual(self.sent[-1][1],card['messagePreview'])

    def test_definitive_refusal_releases_story_admission_without_becoming_unknown(self):
        with patch.object(app_service,'send_notification',side_effect=IntegrationError('notification-not-connected','connect refused')):
            with self.assertRaises(IntegrationError):self.svc.send_digest_now()
        self.assertEqual(len(self.svc._new_stories(self.store.config()['apps']['news'],'private-test-key')),2)
        self.assertEqual(self.store._read('digest-attempts.json',{})['attempts'][0]['state'],'refused')
        self.svc.send_digest_now();self.assertEqual(len(self.sent),1);self.assertEqual(self.svc.digest_recovery(),[])

    def test_provider_echoed_credentials_do_not_enter_exact_payload_journal_or_notification(self):
        self.items[0]['title']='Reflected private-test-key'
        self.svc.send_digest_now()
        self.assertNotIn('private-test-key',self.sent[0][1])
        self.assertNotIn('private-test-key',(self.store.root/'digest-attempts.json').read_text())

    def test_redaction_expansion_is_bounded_before_delivery_admission(self):
        cfg=self.store.config();self.svc.save(cfg['revision'],{key:cfg[key] for key in ('notifications','notes','mcp','apps')},{'apps':{'news':'x'}})
        self.items=[{'id':str(n),'title':'x'*200,'feed':'x'*100,'link':'','summary':''} for n in range(2)]
        with self.assertRaisesRegex(IntegrationError,'sanitized digest'):self.svc.send_digest_now()
        self.assertEqual(self.sent,[]);self.assertIsNone(self.history());self.assertIsNone(self.store._read('digest-attempts.json',None))

    def test_torn_notification_context_refusal_is_definitive_before_dispatch(self):
        with patch.object(app_service,'send_notification',side_effect=IntegrationError('notification-check-required','Save notification settings again.')):
            with self.assertRaises(IntegrationError):self.svc.send_digest_now()
        self.assertEqual(self.sent,[])
        self.assertEqual(self.store._read('digest-attempts.json',{})['attempts'][0]['state'],'refused')
        self.assertEqual(len(self.svc._new_stories(self.store.config()['apps']['news'],'private-test-key')),2)

if __name__=='__main__':unittest.main()
