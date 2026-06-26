## 1. OpenSpec Baseline

- [x] 1.1 Initialize OpenSpec project structure for NeoFlow.
- [x] 1.2 Create change `document-app-builder-excel-template`.
- [x] 1.3 Document proposal, design, tasks, and capability specs for the current requirement change.

## 2. AI Template Builder

- [x] 2.1 Add SDK configuration keys for OpenAI Agents SDK model, API key, base URL, and temperature.
- [x] 2.2 Add backend SDK session routes for create, analyze, confirm, prompt generation, code generation, commit, read, and delete.
- [x] 2.3 Restrict SDK routes to tenant admins and session owners, with super admins allowed to inspect sessions.
- [x] 2.4 Add session models and in-memory session store for the AI template builder workflow.
- [x] 2.5 Add agent orchestration for document analysis, prompt generation, cleaner code generation, and template commit.
- [x] 2.6 Add admin frontend tab for AI-generated templates.
- [x] 2.7 Add structured field draft editing for labels, field keys, types, extraction hints, review rules, examples, and ordering.
- [x] 2.8 Add targeted API and orchestrator tests for SDK route behavior and template commit persistence.

## 3. Fixed Excel Template Output

- [x] 3.1 Add optional Excel template upload to SDK session creation.
- [x] 3.2 Scan uploaded empty Excel templates for `{{field_key}}` placeholders.
- [x] 3.3 Merge Excel placeholder keys into the AI-generated field draft when missing.
- [x] 3.4 Persist Excel template metadata on committed document templates.
- [x] 3.5 Add database migration for fixed Excel output metadata.
- [x] 3.6 Implement Excel template fill service that preserves workbook structure and supports exact and embedded placeholders.
- [x] 3.7 Generate filled Excel output during Feishu push and attach it alongside configured source attachments.
- [x] 3.8 Add tests for placeholder scanning, value filling, zero-value handling, metadata commit, and Feishu attachment generation.

## 4. Review Documentation

- [x] 4.1 Save the original implementation plan into `docs/`.
- [x] 4.2 Create an HTML review page for the OpenAI Agents SDK integration.
- [x] 4.3 Create an interactive HTML demo for the document app builder plan.
- [x] 4.4 Update demo wording so Excel is clearly described as an empty output template, while uploaded images/documents remain the extraction source.

## 5. Verification

- [x] 5.1 Run backend SDK and Excel focused tests.
- [x] 5.2 Run Python compile checks for SDK routes, SDK modules, document helper changes, and template service changes.
- [x] 5.3 Run frontend targeted ESLint for the AI wizard, SDK service, and shared types.
- [x] 5.4 Run frontend production build.
- [x] 5.5 Run `git --no-pager diff --check`.
