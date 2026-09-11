"""Requirement-derived oracle. Missing checks never count as acceptance.

Frozen from M1A L4 revision 21, M1B L5 revision 837, and the user-approved
implementation plan. This module must not import the implementation under test.
"""

SOURCES = {
    'protocol': 'tkos.method/0.1',
    'm1a': {'level': 'L4', 'revision': 21},
    'm1b': {'level': 'L5', 'revision': 837},
    'legacy_base_commit': '1b8cec9cc30e561e3570bc5dd010a09126f283c2',
}

MATRIX = {
    'METHOD-01': [
        'multi_source_signals_traceable', 'potential_issue_revisions_preserved',
        'only_ceo_confirms_strategic_issue', 'research_dri_explicitly_assigned',
    ],
    'METHOD-02': [
        'memo_exact_version_and_sources', 'personal_agent_clarification',
        'two_agent_crosscheck', 'second_clarification_and_direct_human_resolution',
        'research_plan_published_by_designated_dri',
    ],
    'METHOD-03': [
        'report_return_and_resubmission', 'ceo_agent_quality_gate',
        'report_revision_invalidates_prior_precheck', 'meeting_requires_current_passed_precheck',
    ],
    'METHOD-04': [
        'meeting_round_materials_and_raw_evidence', 'two_minutes_versions_and_difference_record',
        'dri_confirms_exact_final_minutes', 'minutes_confirmation_is_not_agreement_confirmation',
        'unmet_goal_requires_another_meeting', 'ceo_confirms_agreement_separately',
    ],
    'METHOD-05': [
        'ceo_decides_adjustment', 'ceo_agent_drafts_exact_target_proposal',
        'co_agent_reviews_impact_and_plan', 'ceo_confirms_formal_update',
        'strategy_and_stable_map_units_effective_atomically',
        'agreement_proposal_and_confirmation_provenance',
    ],
    'METHOD-06': [
        'no_change_completes_round_with_reason', 'no_change_preserves_strategy',
        'no_automatic_issue_closure_or_execution_creation',
        'domain_update_changes_strategic_judgment_only',
    ],
    'METHOD-07': [
        'atomic_business_fact_fields_and_raw_source', 'fact_correction_preserves_original',
        'period_review_records_exact_targets_facts_and_generation',
        'period_review_regeneration_preserves_history',
        'review_has_no_confirmation_or_approval_gate',
        'review_usable_as_analysis_without_changing_targets_or_authority',
    ],
    'METHOD-08': [
        'ltco_advice_and_ceo_return_feedback', 'feedback_revision_rechecked',
        'ceo_confirms_exact_ltco', 'current_effective_strategy_is_explicit_baseline',
        'business_facts_not_copied_into_target_body',
    ],
    'METHOD-09': [
        'pco_and_mission_drafts_form_exact_window_set', 'window_membership_is_explicit',
        'participant_own_comment', 'withdraw_retains_history',
        'replacement_retains_original_and_single_effective_opinion',
        'personal_agent_analysis_is_not_human_comment',
    ],
    'METHOD-10': [
        'close_freezes_effective_opinions', 'closed_window_rejects_comments',
        'one_formal_resolution_per_window', 'resolution_records_choices_and_candidates',
        'ceo_reopen_requires_reason_and_exact_candidate_baseline',
        'ceo_confirms_entire_candidate_set_atomically', 'new_confirmation_has_no_old_all_dri_signature_gate',
    ],
    'METHOD-11': [
        'mission_single_owner_supports_two_outcomes', 'owner_may_equal_target_dri',
        'wrong_pco_version_rejected', 'missing_outcome_rejected',
        'owner_not_duplicated_as_participant', 'support_edges_exist_only_on_mission',
    ],
    'METHOD-12': [
        'm1b_consumes_m1a_effective_strategy_exact_revision',
        'strategy_draft_cannot_replace_formal_basis',
        'strategy_update_appends_impact_notice',
        'confirmed_old_targets_keep_meaning_and_effectiveness',
        'pending_stale_basis_confirmation_rejected', 'new_cycle_uses_new_strategy',
    ],
    'METHOD-13': [
        'context_distinguishes_raw_material_analysis_human_decision_and_review',
        'context_records_adopted_versions_and_exclusion_reasons',
        'role_stage_and_purpose_filter_context',
        'historic_name_and_owner_resolve_at_referenced_revision',
        'collaboration_comments_do_not_revise_business_body',
    ],
    'METHOD-14': [
        'agent_cannot_impersonate_human_comment_or_decision',
        'wrong_responsible_human_rejected', 'unrelated_domain_read_denied',
        'window_read_grant_limited_to_targets_and_comments',
        'cross_domain_source_material_remains_private',
        'revocation_removes_window_grant_and_blocks_prepared_write',
        'revoked_actor_cannot_replay_to_restore_authority',
    ],
    'METHOD-15': [
        'close_and_comment_race_has_serialized_result',
        'concurrent_candidate_confirmation_has_single_winner',
        'same_request_replays_original_receipt', 'same_key_changed_body_conflicts',
        'stale_version_requires_reread',
    ],
    'METHOD-16': [
        'strategy_first_write_failure_rolls_back_map_and_receipt',
        'candidate_first_write_failure_rolls_back_entire_set',
        'stored_unlinked_evidence_is_not_business_success',
        'restart_preserves_objects_receipts_sources_and_runs',
        'restart_replay_has_no_duplicate_effect',
        'run_steps_attempts_and_pause_resume_are_queryable',
    ],
    'METHOD-17': [
        'protocol_bound_server_side_not_request_downgrade',
        'generic_legacy_create_or_propose_cannot_bypass_method',
        'prepare_and_execute_enforce_same_boundaries',
        'new_mission_has_explicit_downstream_handoff_projection',
        'no_implicit_m2_execution_authority',
    ],
    'METHOD-18': [
        'legacy_request_serialization_unchanged',
        'old_a1_a2_a3_and_legacy_delivery_regressions_pass',
        'preupgrade_history_receipts_and_effectiveness_unchanged',
        'new_method_does_not_reinterpret_contract_a',
        'historical_reads_remain_currently_authorized',
    ],
}

REQUIRED_GATES = {
    'source_unchanged', 'fresh_database_from_accepted_base',
    'migration_replay', 'ordinary_application_privileges',
    'immutable_history', 'raw_evidence_storage', 'no_external_effects',
    'existing_environment_preserved',
}

EXCLUDED = [
    'clark_pages', 'production_identity_integration', 'historical_production_migration',
    'deployment', 'real_agent_research_quality', 'real_business_outcomes',
]
