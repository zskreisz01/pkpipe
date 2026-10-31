---
spec_id: [###-feature-name]
title: [FEATURE NAME]
status: Draft   # Draft | Active | On-hold | Completed | Obsolete
status_reason:   # required for On-hold / Obsolete
description: [2-3 sentences: what changes, for whom]
relevant_when: [topic, module, keyword]
blocked_by: []
superseded_by: []
related: []
updated: [YYYY-MM-DD]
---

# Feature Specification: [FEATURE NAME]

**Feature Branch**: `spec/[###-feature-name]`
**Created**: [DATE]
**Status**: Draft
**Input**: [design doc section / process model page / decision]

## User Scenarios & Testing *(mandatory)*

### User Story 1 - [Brief Title] (Priority: P1)

[Description from the owner's or a client's point of view]

**Why this priority**: [...]

**Independent Test**: [how it is tested on its own: pytest, eval suite, flow test with fakes]

**Acceptance Scenarios**:

1. **Given** [state], **When** [action], **Then** [outcome]

### Edge Cases

- [...]

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST [...]
- **FR-00x**: [NEEDS CLARIFICATION: ...]

### Key Entities

- **[Entity]**: [what it represents]

## Success Criteria *(mandatory)*

- **SC-001**: [measurable outcome]

## Clarifications

### Session [DATE]

- Q: [...] → A: [...]

## Out of Scope

- [...]
