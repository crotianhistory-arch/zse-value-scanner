# Activity taxonomy v0.1 — energy/electrical pilot

This taxonomy is an **analytical mapping layer**, not a reported accounting classification and not a peer ranking.

Its purpose is to normalize heterogeneous annual-report descriptions into reviewable multi-label business activities while preserving exact source-fact provenance.

Initial branches:

- Energy infrastructure
  - Electrical grid
    - Grid solutions
    - Grid equipment
      - Transformers
      - Switchgear
      - Substations
      - Grid automation
    - Electrical distribution
    - Secure power
    - Energy management
    - Power cables
      - High-voltage power cables
      - Medium/low-voltage power cables
      - Submarine power cables
      - Power-cable accessories
  - Distributed energy
    - Energy storage
    - EV charging
  - Renewable generation
    - Wind energy
      - Wind turbines
      - Wind power plants
      - Wind services
      - Wind project development
  - Electrification
- Automation
  - Industrial automation
  - Building automation
- Digital/connectivity infrastructure
  - Data-centre infrastructure
  - Digital solutions
  - Telecom cables

## Evidence policy

- `R2_REPORTED_TEXT`: issuer-reported tagged XBRL text from the v0.4.4 annual baseline.
- `A1_ANALYTICAL_ACTIVITY_MAPPING`: a deterministic rule maps reported text to a taxonomy node. The exact rule ID, matched phrase, source fact ID, concept, period, language and excerpt are retained.
- `A2_DERIVED_TAXONOMY_ANCESTOR`: a parent category derived solely from the taxonomy hierarchy. It is not a separately reported activity.

No exposure percentage is inferred. No whole-company/segment equivalence is inferred. No peer score or rank is produced.

The initial rules are intentionally conservative and pilot-focused. They are expected to expand by sector and language after manual review of additional annual reports.
