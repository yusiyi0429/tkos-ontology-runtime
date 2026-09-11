"""Required assertions frozen from 契约包A / 独立验收契约 before implementation.

Optional withdrawal and profile migration are explicitly not delivered in A2.
An unknown-action rejection proves the fence only, never those capabilities.
"""
MATRIX = {
    'A2-01': ['r3_manifest_exact', 'all_current_signers', 'single_atomic_activation', 'no_execution_release'],
    'A2-02': ['signing_keeps_content', 'stale_head_rejected', 'fresh_head_same_manifest'],
    'A2-03': ['draft_not_formal', 'unadopted_document_not_binding'],
    'A2-04': ['r2_invalidates_r1', 'r3_does_not_revive_r1', 'new_r3_all_resign', 'history_retained'],
    'A2-05': ['add_invalidates_old_set', 'new_member_submission_required', 'all_four_resign',
              'remove_invalidates_old_set', 'amend_authority_cas_fixed_profile_period', 'active_amend_rejected'],
    'A2-06': ['implicit_source_revision_invalidates', 'informational_change_preserves',
              'new_binding_formal_submission', 'closure_limit_rejects'],
    'A2-07': ['capacity_three_passes', 'capacity_two_confirm_rejected', 'capacity_two_activate_rejected',
              'fail_and_unknown_rejected', 'cycle_rejected'],
    'A2-08': ['missing_signer_rejected', 'duplicate_human_rejected', 'agent_rejected',
              'wrong_assignment_rejected', 'old_manifest_rejected', 'duplicate_vote_no_extra_weight'],
    'A2-09': ['unknown_withdrawal_rejected'],
    'A2-10': ['required_revoke_rejected', 'unrelated_revoke_allows', 'confirmation_history_retained'],
    'A2-11': ['required_expiry_at_final_barrier', 'ceo_valid_epoch_unchanged', 'complete_rollback'],
    'A2-12': ['generic_rebind_rejected', 'injected_profile_rejected', 'unused_profile_not_migration'],
    'A2-13': ['same_cas_one_winner', 'activation_one_winner', 'idempotent_original_receipt', 'no_double_effect'],
    'A2-14': ['amend_first_rejected', 'source_first_rejected', 'revoke_first_rejected', 'lock_order_observed'],
    'A2-15': ['activate_first_history_commits', 'waiting_amend_rejected',
              'later_source_and_revoke_preserve_history', 'lock_order_observed'],
    'A2-16': ['first_member_write_checkpoint', 'all_tables_rollback', 'retry_single_activation'],
    'A2-17': ['second_same_period_round_rejected', 'capacity_not_double_reserved'],
    'A2-18': ['complete_published_manifest_readable', 'private_direct_hidden', 'private_relation_hidden',
              'private_prepare_hidden', 'no_hidden_count_or_names'],
}
CAPABILITIES_EXCLUDED = ['withdraw_confirmation', 'profile_migration', 'baseline_suspension',
                         'A3_execution_authority', 'IC_handover', 'remote_deployment']
