## ADDED Requirements

### Requirement: Empty Excel template uses field_key placeholders

The system SHALL support an optional empty Excel output template whose text cells may contain `{{field_key}}` placeholders.

#### Scenario: Upload Excel template with placeholders

- **WHEN** an admin uploads an Excel output template containing `{{order_no}}` and `{{quantity}}`
- **THEN** the system scans the workbook and returns placeholder metadata including sheet name, cell coordinate, field key, and raw cell value

#### Scenario: Upload source document without Excel template

- **WHEN** an admin creates a builder session without an Excel output template
- **THEN** the system continues the AI template builder flow without Excel placeholder metadata

### Requirement: Excel placeholders are field draft fill targets

The system SHALL treat Excel placeholders as fill targets whose values come from the uploaded extraction source document.

#### Scenario: Placeholder key missing from AI draft

- **WHEN** the Excel template contains a `field_key` that was not detected by document analysis
- **THEN** the system adds a review-enforced text field draft for that `field_key`

#### Scenario: Placeholder key already exists in AI draft

- **WHEN** the Excel template contains a `field_key` already present in the generated field draft
- **THEN** the system does not create a duplicate field

### Requirement: Published template stores Excel output metadata

The system SHALL store fixed Excel output metadata on the committed document template.

#### Scenario: Commit session with Excel template

- **WHEN** an admin commits a confirmed builder session with an uploaded Excel template
- **THEN** the template stores output mode `both`, original Excel file name, Excel template storage path, and scanned placeholder metadata

#### Scenario: Commit session without Excel template

- **WHEN** an admin commits a confirmed builder session without an Excel template
- **THEN** the template stores output mode `bitable`

### Requirement: Excel output is generated from extraction data

The system SHALL fill a copied Excel template with extraction result values by matching `{{field_key}}` placeholders.

#### Scenario: Exact placeholder cell

- **WHEN** a cell value is exactly `{{order_no}}` and extraction data contains `order_no`
- **THEN** the generated workbook cell value is the raw extracted value

#### Scenario: Embedded placeholder cell

- **WHEN** a cell value contains text around `{{quantity}}` and extraction data contains numeric value `0`
- **THEN** the generated workbook preserves surrounding text and inserts `0` rather than an empty string

#### Scenario: Missing extracted value

- **WHEN** a placeholder has no matching extraction value
- **THEN** the generated workbook leaves the exact-placeholder cell empty or the embedded-placeholder segment empty and records the missing field for warning logs

### Requirement: Fixed Excel output preserves workbook layout

The system SHALL preserve workbook layout and styling while filling placeholders.

#### Scenario: Fill styled workbook

- **WHEN** the template workbook contains styles and merged cells
- **THEN** the generated workbook keeps those styles and merged cells after placeholder replacement

### Requirement: Fixed Excel output attaches to Feishu push

The system SHALL attach the generated fixed Excel output file to the Feishu record when a template is configured for fixed Excel output and Feishu push is configured.

#### Scenario: Template output mode both

- **WHEN** a processed document is pushed to Feishu with template output mode `both`
- **THEN** the system uploads the generated Excel file as an attachment in addition to any configured source attachments

#### Scenario: Excel generation fails

- **WHEN** fixed Excel generation fails during Feishu push
- **THEN** the system logs a warning and continues the existing Feishu push flow without the generated Excel attachment

#### Scenario: Template output mode bitable

- **WHEN** a processed document is pushed with template output mode `bitable`
- **THEN** the system does not generate a fixed Excel output attachment
