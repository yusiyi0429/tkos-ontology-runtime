"""Real HTTP concurrency with observed PostgreSQL blocking and explicit barriers.

Thread launch order is not treated as lock-order evidence. The two test servers
use distinct PostgreSQL application_name values so the observer can prove that
the second command is blocked by the first server's transaction.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import threading
import time
import uuid

from acceptance.runtime.client import Client
from acceptance.protocol_a1_independent.support import wait


def race(h, f, url: str, actor: str, commands: list[dict]) -> list[dict]:
    gate=threading.Barrier(len(commands))
    def run(index,body):
        client=Client(url,f['actors'][actor]['token'],f'{actor}-race-{index}',h.log)
        try:
            gate.wait(timeout=5)
            result=client.request('POST','/v1/actions',deepcopy(body),expected=(200,409))
            return {'status':result.status_code,'body':result.json()}
        finally:client.close()
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        futures=[executor.submit(run,i,body) for i,body in enumerate(commands)]
        return [future.result(timeout=40) for future in futures]


def reached(h, token: str):
    path=h.control/(token+'.reached.json')
    return wait(lambda: json.loads(path.read_text()) if path.exists() else None,timeout=25)


def ordered(h, f, *, first_url: str, second_url: str, first_actor: str,
            second_actor: str, first_body: dict, second_body: dict,
            first_db_name: str, second_db_name: str,
            first_status=200, second_status=(200,409,403),
            checkpoint='auth_fence_acquired') -> dict:
    token='a2-order-'+uuid.uuid4().hex
    first=Client(first_url,f['actors'][first_actor]['token'],first_actor+'-first',h.log)
    second=Client(second_url,f['actors'][second_actor]['token'],second_actor+'-second',h.log)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future1=executor.submit(first.request,'POST','/v1/actions',deepcopy(first_body),
            expected=first_status,headers=h.headers(checkpoint,mode='barrier',token=token))
        future2=None
        try:
            barrier=reached(h,token)
            future2=executor.submit(second.request,'POST','/v1/actions',deepcopy(second_body),expected=second_status)
            def blocked():
                rows=h.sql(f,'''SELECT waiter.pid AS waiting_pid, blocker.pid AS blocking_pid,
                    waiter.wait_event_type, waiter.wait_event, waiter.application_name AS waiting_application,
                    blocker.application_name AS blocking_application
                    FROM pg_stat_activity waiter CROSS JOIN LATERAL unnest(pg_blocking_pids(waiter.pid)) p(pid)
                    JOIN pg_stat_activity blocker ON blocker.pid=p.pid
                    WHERE waiter.datname=current_database() AND blocker.datname=current_database()
                    AND waiter.application_name=%s AND blocker.application_name=%s
                    AND waiter.wait_event_type='Lock' ''',(second_db_name,first_db_name))
                return rows if rows else None
            lock_evidence=wait(blocked,timeout=12)
            assert not future2.done(),'second HTTP completed before lock barrier released'
            released_at=datetime.now(timezone.utc).isoformat()
            (h.control/(token+'.release')).touch(mode=0o600)
            r1=future1.result(timeout=30);r2=future2.result(timeout=30)
            return {'first_barrier':barrier,'postgres_blocking':lock_evidence,'released_at':released_at,
                    'first':{'status':r1.status_code,'body':r1.json()},
                    'second':{'status':r2.status_code,'body':r2.json()}}
        finally:
            (h.control/(token+'.release')).touch(mode=0o600,exist_ok=True)
            # Release before joining, even on failed observation. Never leave a
            # scope lock held by a forgotten acceptance process.
            for future in (future1,future2):
                if future:
                    try:future.result(timeout=35)
                    except Exception:pass
            first.close();second.close()


def expire_at_admission(h,f,url: str,body: dict) -> dict:
    token='a2-expiry-'+uuid.uuid4().hex
    client=Client(url,f['actors']['ceo']['token'],'ceo-final-expiry',h.log)
    before=h.snapshot(f)
    epoch=h.sql(f,'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s',(f['scope_id'],))[0]['auth_epoch']
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(client.request,'POST','/v1/actions',body,expected=(403,409),
            headers=h.headers('before_business_commit',mode='barrier',token=token))
        try:
            barrier=reached(h,token)
            def expired():
                rows=h.sql(f,'''SELECT assignment_id::text,valid_to,clock_timestamp() AS observed_at,
                    valid_from<=clock_timestamp() AND (valid_to IS NULL OR clock_timestamp()<valid_to) AS valid
                    FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=ANY(%s::uuid[])''',
                    (f['scope_id'],[f['actors']['b']['assignment_id'],f['actors']['ceo']['assignment_id']]))
                by_id={row['assignment_id']:row for row in rows}
                if len(by_id)==2 and not by_id[f['actors']['b']['assignment_id']]['valid']:
                    assert by_id[f['actors']['ceo']['assignment_id']]['valid'] is True
                    return rows
                return None
            clock_evidence=wait(expired,timeout=30)
            (h.control/(token+'.release')).touch(mode=0o600)
            response=future.result(timeout=20)
            after_epoch=h.sql(f,'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s',(f['scope_id'],))[0]['auth_epoch']
            assert after_epoch==epoch and h.snapshot(f)==before
            assert response.json()['error']['code'] in {'FORBIDDEN','CONFIRMATION_INCOMPLETE'}
            return {'barrier':barrier,'clock_evidence':clock_evidence,'auth_epoch':epoch,
                    'epoch_unchanged':True,'all_governed_state_unchanged':True,
                    'status':response.status_code,'error':response.json()['error']['code']}
        finally:
            (h.control/(token+'.release')).touch(mode=0o600,exist_ok=True)
            try:future.result(timeout=35)
            except Exception:pass
            client.close()
