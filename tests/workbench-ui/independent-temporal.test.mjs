import { test } from 'node:test';
import assert from 'node:assert/strict';
import { timeMicros, currentConditions, snapshotConditions, conditionsMatch } from '../../workbench/lib/snapshot.js';
const id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const current = (time) => currentConditions({validAt:time,knownAt:time,objectIds:[id]});
const snapshot = (time) => snapshotConditions({valid_at:time,known_at:time,selected:[{object_id:id}],excluded:[]});
test('timestamps differing by 806 microseconds are different audit conditions',()=>{
 assert.equal(conditionsMatch(current('2026-09-09T09:08:21.233Z'),snapshot('2026-09-09T09:08:21.233806+00:00')).same,false);
 assert.equal(conditionsMatch(current('2026-09-09T09:08:21.233Z'),snapshot('2026-09-09T17:08:21.233000+08:00')).same,true);
});
test('incomplete time input cannot match a persisted snapshot',()=>{
 assert.equal(conditionsMatch(current(''),snapshot('2026-09-09T09:08:21Z')).same,false);
 assert.equal(conditionsMatch(current('not-a-time'),snapshot('2026-09-09T09:08:21Z')).same,false);
});
test('microsecond keys retain time before the Unix epoch and timezone equivalence',()=>{
 assert.equal(timeMicros('1969-12-31T23:59:59.123456Z'),'-876544');
 assert.equal(timeMicros('2026-09-09T09:08:21.233806Z'),timeMicros('2026-09-09T17:08:21.233806+08:00'));
});
