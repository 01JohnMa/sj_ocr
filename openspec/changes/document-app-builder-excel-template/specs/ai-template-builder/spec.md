## ADDED Requirements

### Requirement: Admin-only AI template builder entry

The system SHALL provide an AI-assisted template builder only to tenant admins and super admins inside the admin configuration area.

#### Scenario: Tenant admin opens builder

- **WHEN** a tenant admin opens the admin configuration area
- **THEN** the system displays an AI template generation entry for the selected tenant

#### Scenario: Regular user calls builder API

- **WHEN** a non-admin user calls an AI template builder API
- **THEN** the system rejects the request with an authorization error

### Requirement: Builder session starts from an extraction source document

The system SHALL require an image or document as the extraction source when creating an AI template builder session.

#### Scenario: Admin uploads source document

- **WHEN** an admin uploads a supported image or document to create a builder session
- **THEN** the system stores the file, runs OCR, and returns a session containing OCR text, confidence, page count, file name, and state `ocr_completed`

#### Scenario: Source document OCR fails

- **WHEN** OCR fails while creating a builder session
- **THEN** the system cleans up uploaded session files and surfaces the processing error

### Requirement: Field draft is generated before template commit

The system SHALL generate a structured field draft from OCR text before any template is written to permanent template configuration.

#### Scenario: Analyze session

- **WHEN** an admin requests analysis for an OCR-completed session
- **THEN** the system returns recommended document type, document code, tenant suggestion, detected fields, and suggested examples

#### Scenario: Commit without confirmed template

- **WHEN** an admin attempts to commit a session before confirming template fields
- **THEN** the system rejects the operation and does not create a document template

### Requirement: Admin reviews and edits field schema

The system SHALL allow admins to edit field label, `field_key`, field type, extraction hint, review enforcement, allowed review values, sample value, example input, and example output before commit.

#### Scenario: Duplicate field keys

- **WHEN** the field draft contains duplicate `field_key` values
- **THEN** the frontend prevents commit and shows a validation message

#### Scenario: Invalid example output

- **WHEN** an example output is not valid JSON
- **THEN** the frontend prevents commit and marks the invalid example

### Requirement: Confirmed builder session commits template configuration

The system SHALL create template, field, example, extraction prompt, and cleaner metadata from a confirmed builder session.

#### Scenario: Commit confirmed session

- **WHEN** an admin commits a confirmed builder session
- **THEN** the system creates a document template, creates ordered template fields, creates active examples, stores generated prompt metadata, and returns created identifiers

#### Scenario: Existing tenant context

- **WHEN** the confirmed session includes an existing tenant id
- **THEN** the system creates the template under that tenant without creating a new tenant

### Requirement: Builder sessions are owner scoped

The system SHALL allow only the session owner or a super admin to read, mutate, or delete an AI template builder session.

#### Scenario: Another tenant admin accesses session

- **WHEN** a tenant admin who does not own a builder session requests it
- **THEN** the system rejects the request with an authorization error

#### Scenario: Owner deletes session

- **WHEN** the session owner deletes a builder session
- **THEN** the system removes stored session files and deletes the session record
