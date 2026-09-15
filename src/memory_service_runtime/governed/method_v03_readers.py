"""Authorized Anchor work surfaces and advisory, read-only action checks."""
from . import method_readers, lifecycle_readers, service
from .models import ActionRequest
from .errors import GovernedError


def operations(conn,ctx,obj):
    kind=obj['object_type']
    params={'reason':'Read-only availability check'}
    action={'StrategicArchitecture':'method_confirm_architecture',
            'OperatingState':'method_confirm_state',
            'OperatingProblem':'method_close_problem',
            'PotentialIssue':'m1a_confirm_strategic_issue'}.get(kind)
    if not action:
        return lifecycle_readers.operations(conn,ctx,obj)
    if not obj['latest_revision']:
        return [{'action_type':action,'allowed':False,'reason':'current_version_not_authorized'}]
    if kind=='OperatingProblem':
        params.update(disposition='resolved',evidence_refs=[obj['latest_revision']['payload']['state_ref']])
    request=ActionRequest.model_validate({'action_type':action,'contract_version':'tkos.method/0.3',
        'target':{'object_id':obj['object_id'],'revision_id':obj['latest_revision_id'],'expected_version':obj['object_version']},
        'expected_versions':[],'params':params,'idempotency_key':'readonly-availability-check','reason':'Read-only availability check'})
    reason=None
    try:
        service.prepare_action(conn,ctx,request)
    except GovernedError as exc:
        reason=exc.code
    return [{'action_type':action,'allowed':reason is None,'reason':reason,
             'required_user_input':['reason','disposition','evidence_refs'] if kind=='OperatingProblem' else ['reason'],
             'authority':'advisory_rechecked_on_submit'}]


def read(conn,ctx,object_id):
    obj=method_readers.object_state(conn,ctx,object_id)
    if obj['protocol']['contract_version']!='tkos.method/0.3':
        raise GovernedError('PROTOCOL_BINDING_CONFLICT')
    obj['operations']=operations(conn,ctx,obj)
    obj['history']=method_readers.review_records(conn,ctx,object_id)['items']
    obj['refresh_interval_seconds']=5
    return obj
