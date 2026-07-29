# Phase 0 migration matrix

Generated from the active migration files at `591973ff`, the canonical `django_migrations` table read in a read-only transaction, and locally available Git objects. No recovered historical file is activated by this matrix.

| App | Migration recorded in DB | File/source | State/model evidence | Dependencies | Classification | Proposed action |
|---|---|---|---|---|---|---|
| editorial | `0001_initial` | integration_worktree | Contributor, Edition, EditionPipeline, EditionText, ID, Language, Seal, Work, edition | unknown/none | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0002_editionpipeline_translation_language` | integration_worktree | editionpipeline | editorial.0001_initial | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0003_edition_frontmatter_fields` | integration_worktree | edition | editorial.0002_editionpipeline_translation_language | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0004_alter_edition_country` | integration_worktree | edition | editorial.0003_edition_frontmatter_fields | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0005_edition_about_edition_text` | integration_worktree | unknown | editorial.0004_alter_edition_country | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0006_editionpipeline_core_last_txt_path` | integration_worktree | editionpipeline | editorial.0005_edition_about_edition_text | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0007_editionpipeline_md_language` | integration_worktree | editionpipeline | editorial.0006_editionpipeline_core_last_txt_path | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0008_edition_cover_filepath` | integration_worktree | edition | editorial.0007_editionpipeline_md_language | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0009_editionpipeline_frontmatter_language_and_more` | integration_worktree | editionpipeline | editorial.0008_edition_cover_filepath | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0010_edition_about_contributor_template_and_more` | integration_worktree | edition | editorial.0009_editionpipeline_frontmatter_language_and_more | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0011_edition_lock_polish_edition_lock_refine_and_more` | integration_worktree | edition | editorial.0010_edition_about_contributor_template_and_more | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0012_pipelineartifact` | integration_worktree | ID, PipelineArtifact | editorial.0011_edition_lock_polish_edition_lock_refine_and_more | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0013_alter_edition_copyright_template` | integration_worktree | edition | editorial.0012_pipelineartifact | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0013_edition_introduction_epilogue_text` | local_git_object `22ea7008ab35` | edition | editorial.0012_pipelineartifact | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0013_pipelineartifact_status_sha256` | local_git_object `43638afb41c1` | pipelineartifact | editorial.0012_pipelineartifact | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0014_edition_language_variant` | local_git_object `ba5d724fd86c` | edition | editorial.0013_edition_introduction_epilogue_text | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0014_editionpipeline_refine_profile` | integration_worktree | editionpipeline | editorial.0013_alter_edition_copyright_template | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0015_alter_edition_copyright_template_labels` | integration_worktree | edition | editorial.0014_editionpipeline_refine_profile | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0015_merge_20260127_1950` | local_git_object `43638afb41c1` | unknown | editorial.0013_pipelineartifact_status_sha256, editorial.0014_edition_language_variant | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0016_editionpipeline_build_outdated_and_more` | integration_worktree | EditionBuild, ID, edition, editionpipeline | editorial.0015_alter_edition_copyright_template_labels | `PRESENT_AND_MATCHING` | keep active graph |
| editorial | `0016_pipelineartifact_merge_translate_stage` | local_git_object `43638afb41c1` | pipelineartifact | editorial.0015_merge_20260127_1950 | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0017_edition_copyright_editorial_fields` | local_git_object `43638afb41c1` | edition | editorial.0016_pipelineartifact_merge_translate_stage | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0017_editionpipeline_last_version` | preserved_main_untracked | editionpipeline | editorial.0016_editionpipeline_build_outdated_and_more | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0017_editionpipeline_markitdown_stages` | not_found | unknown | unknown/none | `APPLIED_FILE_MISSING` | source required or explicit controlled-baseline decision |
| editorial | `0018_edition_copyright_holder_fields` | local_git_object `43638afb41c1` | edition | editorial.0017_edition_copyright_editorial_fields | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0019_editionblock` | local_git_object `509e89c433e9` | EditionBlock, ID | editorial.0018_edition_copyright_holder_fields | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0020_work_projects_fields` | local_git_object `9f17867ae38c` | work | editorial.0019_editionblock | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0021_alter_edition_language_code_and_more` | local_git_object `f6f761d7ab38` | edition, editionpipeline, pipelineartifact | editorial.0020_work_projects_fields | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0022_edition_book_id_edition_canonical_official_tag_and_more` | local_git_object `b06df54dce31` | edition | editorial.0021_alter_edition_language_code_and_more | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0023_alter_edition_status` | local_git_object `b06df54dce31` | edition | editorial.0022_edition_book_id_edition_canonical_official_tag_and_more | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0024_alter_edition_status` | local_git_object `d962ef777031` | edition | editorial.0023_alter_edition_status | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| editorial | `0025_alter_editionpipeline_current_stage` | local_git_object `d962ef777031` | editionpipeline | editorial.0024_alter_edition_status | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| intake_module | `0001_initial` | integration_worktree | ID, IntakeBatch, IntakeItem, intakeitem | unknown/none | `PRESENT_AND_MATCHING` | keep active graph |
| intake_module | `0002_intakeitem_pipeline_handoff` | integration_worktree | intakeitem | intake_module.0001_initial | `PRESENT_AND_MATCHING` | keep active graph |
| intake_module | `0003_intakeitem_duplicate_of` | integration_worktree | intakeitem | intake_module.0002_intakeitem_pipeline_handoff | `PRESENT_AND_MATCHING` | keep active graph |
| intake_module | `0004_translationjob_and_identity_constraints` | integration_worktree | ID, TranslationJob, intakeitem, translationjob | editorial.0016_editionpipeline_build_outdated_and_more, intake_module.0003_intakeitem_duplicate_of | `PRESENT_AND_MATCHING` | keep active graph |
| intake_module | `0005_translationjob_warning_confirmation` | integration_worktree | translationjob | intake_module.0004_translationjob_and_identity_constraints | `PRESENT_AND_MATCHING` | keep active graph |
| intake_module | `0006_book_code_allocation` | integration_worktree | BookCodeSequence, intakebatch, intakeitem | intake_module.0005_translationjob_warning_confirmation | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0001_initial` | integration_worktree | ID, PipelineJob | unknown/none | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0002_alter_pipelinejob_options_and_more` | integration_worktree | pipelinejob | pipeline.0001_initial | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0003_alter_pipelinejob_stage_bookeditiontemplate` | integration_worktree | BookEditionTemplate, ID, pipelinejob | pipeline.0002_alter_pipelinejob_options_and_more | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0004_bookeditiontemplate_collection_name_and_more` | integration_worktree | bookeditiontemplate | pipeline.0003_alter_pipelinejob_stage_bookeditiontemplate | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0005_alter_bookeditiontemplate_collaborator_roles` | integration_worktree | bookeditiontemplate | pipeline.0004_bookeditiontemplate_collection_name_and_more | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0006_alter_bookeditiontemplate_collaborator_roles` | integration_worktree | bookeditiontemplate | pipeline.0005_alter_bookeditiontemplate_collaborator_roles | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0007_bookeditiontemplate_text_source_mode` | integration_worktree | bookeditiontemplate | pipeline.0006_alter_bookeditiontemplate_collaborator_roles | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0008_textsnapshot` | integration_worktree | ID, TextSnapshot | editorial.0001_initial, pipeline.0007_bookeditiontemplate_text_source_mode | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0009_bookeditiontemplate_frontmatter_fields` | integration_worktree | bookeditiontemplate | pipeline.0008_textsnapshot | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0010_bookeditiontemplate_city_country` | integration_worktree | bookeditiontemplate | pipeline.0009_bookeditiontemplate_frontmatter_fields | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0011_alter_bookeditiontemplate_language` | integration_worktree | bookeditiontemplate | pipeline.0010_bookeditiontemplate_city_country | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0012_align_bookeditiontemplate_schema` | integration_worktree | bookeditiontemplate | pipeline.0011_alter_bookeditiontemplate_language | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0012_bookeditiontemplate_editorial_name` | local_git_object `78a254f34c88` | bookeditiontemplate | pipeline.0011_alter_bookeditiontemplate_language | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| pipeline | `0013_bookeditiontemplate_copyright_fields` | local_git_object `78a254f34c88` | bookeditiontemplate | pipeline.0012_bookeditiontemplate_editorial_name | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| pipeline | `0013_ensure_bookeditiontemplate_columns` | integration_worktree | RunPython | pipeline.0012_align_bookeditiontemplate_schema | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0014_bookeditiontemplate_registration_upload_fields` | integration_worktree | bookeditiontemplate | pipeline.0013_ensure_bookeditiontemplate_columns | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0014_pipelinerun` | local_git_object `78a254f34c88` | ID, PipelineRun, PipelineRunItem | pipeline.0013_bookeditiontemplate_copyright_fields | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| pipeline | `0015_alter_bookeditiontemplate_language_and_more` | local_git_object `78a254f34c88` | bookeditiontemplate, pipelinejob, pipelinerun | pipeline.0014_pipelinerun | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| pipeline | `0015_bookeditiontemplate_epilogue_text_and_more` | integration_worktree | bookeditiontemplate | pipeline.0014_bookeditiontemplate_registration_upload_fields | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0016_alter_pipelinerun_action` | local_git_object `78a254f34c88` | pipelinerun | pipeline.0015_alter_bookeditiontemplate_language_and_more | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| pipeline | `0016_official_body_models` | integration_worktree | ID, OfficialBodyPromotion, OfficialBodySnapshot, officialbodysnapshot | intake_module.0004_translationjob_and_identity_constraints, pipeline.0015_bookeditiontemplate_epilogue_text_and_more | `PRESENT_AND_MATCHING` | keep active graph |
| pipeline | `0016_pipelinejob_markitdown_stages` | not_found | unknown | unknown/none | `APPLIED_FILE_MISSING` | source required or explicit controlled-baseline decision |
| pipeline | `0017_bookeditiontemplate_historic_fields` | not_found | unknown | unknown/none | `APPLIED_FILE_MISSING` | source required or explicit controlled-baseline decision |
| pipeline | `0017_pipelinerunstate` | local_git_object `63feed2732fa` | ID, PipelineRunState | editorial.0012_pipelineartifact, pipeline.0016_alter_pipelinerun_action | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| pipeline | `0018_pipelinerunstate_modes_and_md_fields` | local_git_object `78a254f34c88` | pipelinerunstate | pipeline.0017_pipelinerunstate | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
| pipeline | `0019_alter_pipelinerun_action` | local_git_object `78a254f34c88` | pipelinerun | pipeline.0018_pipelinerunstate_modes_and_md_fields | `APPLIED_FILE_MISSING` | preserve evidence; do not activate partial branch |
