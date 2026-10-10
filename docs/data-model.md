# Canonical Data Model

- **Variant**: canonical identity + preferred GRCh38 coordinate for milestone 1. Later add `variant_placement` for assembly-specific mappings.
- **VariantAlias**: rsID aliases, merged IDs, HGVS, SPDI, source-local IDs.
- **Trait**: normalized phenotype concept; retain source-reported names in `trait_synonym` / provenance.
- **Study**: accession/publication metadata.
- **Population**: normalized sample/ancestry descriptors while retaining source metadata.
- **Association**: statistical variant-trait observation in study/population context.
- **AssociationSource**: many-to-many provenance between canonical association and upstream source record/run.

Candidate later tables: `variant_placement`, `allele_frequency`, `ontology_term`, `ontology_edge`, `study_cohort`, `measurement_definition`, `summary_statistic_file`, `gene_annotation`, `publication`, `license`.

## GWAS normalization (checkpoint 1)

Normalization preserves one source association as one DTO. `traits` retains every
mapped ontology ID/name, `reported_traits` retains the actual reported phenotype
labels, and `variants` retains rsID/effect-allele pairs. The singular `trait`,
`variant_rsid`, and `effect_allele` convenience fields are populated only when
their respective mappings are unambiguous. No phenotype is inferred from the
queried variant; rs4988235 associations are not all lactose-related.

Missing values remain `None` or empty collections, including unknown alleles
such as `?`. A reported-only trait has no invented ontology ID. Missing source
IDs are not replaced with process-dependent hashes. Malformed fields, conflicting
representations, and unresolved cardinality produce explicit findings through
`validate_association`; the original payload remains in `raw`. Multi-valued
observations require a deliberate modeling decision before canonical loading,
not an automatic Cartesian product.

For descriptive beta values, `effect_size` is the source numeric magnitude;
`effect_direction` and `effect_size_unit` are separate. A bare signed beta keeps
its sign. `effect_size_text` and the reported `confidence_interval` preserve the
source representation. Do not interpret a decrease magnitude as a positive
signed beta or interpret its reported interval as a signed canonical interval.

P-values use exact `Decimal` construction from source mantissa/exponent when
available, with `p_value_text` retaining that scientific representation.
Decode saved JSON with `json.loads(..., parse_float=Decimal)` for lossless decimal
tokens. A float-only input without mantissa/exponent produces a precision warning;
conflicting representations produce an error. Zero is not a valid exact p-value:
a source zero needs an explicit censored/underflow representation, not acceptance
as exact zero.

The saved 68-association case study contains nine disagreements between numeric
`p_value` tokens and mantissa/exponent pairs, including a numeric zero whose pair
represents `3e-1451`. Normalization retains the pair-derived exact value, preserves
the conflicting token in `raw`, and emits `ASSOC_CONFLICTING_PVALUE` errors.
These require an explicit source-reconciliation policy before loading; do not
silently discard the findings because the discrepancies look like float artifacts.

## PostgreSQL persistence (checkpoint 2)

The repository has 16 ORM tables (the earlier 17-table count was inaccurate).
Alembic revision `0001` freezes the original schema; `0002` migrates p-values and
effect sizes to unconstrained PostgreSQL `NUMERIC` and adds observation identity,
raw snapshots, quality lineage, and integrity constraints. The migration cannot
recover precision already lost in an older database: re-acquire source evidence.
Existing invalid/zero p-values or incompatible provenance cause migration failure,
not automatic repair. Downgrade refuses to discard captured evidence or precision.

### Loading policy

`etl.loading.load_gwas_associations(engine, dtos, release_key=...)` owns its
transactions. Input DTOs must agree with normalization of their attached source
payloads, including findings. It does not connect discovery to loading or provide
the ingestion/replay CLI; that is checkpoint 3.

- Exactly one valid variant and one trait are required. Multi-trait,
  multi-variant, haplotype, and interaction records are quarantined; no Cartesian
  products or arbitrary choices are generated.
- Missing effect alleles, effect estimates, study accessions, ontology IDs, or
  p-values may remain missing. A trait without an ontology ID has a source-scoped
  name identity. Missing study accessions do not create invented study records.
- GWAS records without assembly evidence create variants with `assembly=NULL`,
  not an inferred GRCh38 placement. The existing GraphQL assembly field is made
  nullable solely for this schema compatibility; no API queries are expanded.
- Conflicting p-value representations retain the reported association but set
  `association.p_value=NULL`, `p_value_status='unreconciled'`. Exact candidate
  values are held in `raw_object.p_value_candidate`, never treated as reconciled
  scientific values. `p_value_text`, mantissa, and exponent remain textual.
- PostgreSQL numeric supports up to 131072 integer and 16383 fractional digits.
  Out-of-range values retain their text and findings but not a numeric value.
  PostgreSQL 17 integration tests explicitly verify nonzero `3e-1451`, the
  `1e-16383` boundary, and rejection of `1e-16384`.
- Other error findings prevent canonical loading. Quarantine is a persisted
  `raw_object.disposition`, with the full payload and normalized collection
  available for review, not an empty success response or lost file.

### Identity and provenance

Variants share normalized rsID identity; traits share ontology IDs when available;
studies share GWAS accessions. UUIDv5 identities are deterministic. Association
identity is a source-scoped observation key derived from GWAS association ID,
not a biological claim that matching variant/trait pairs from different studies
are duplicates. Cross-source scientific deduplication is deliberately deferred.

Canonical/source links are immutable first-observation records. Repeated loading
does not create additional associations or `association_source` links. A changed
payload for an already linked source ID is quarantined; it cannot silently replace
the previous scientific observation. Study upserts can fill missing metadata,
while conflicting nonmissing annotations and ontology labels produce findings
and retain incoming evidence in raw snapshots.

Every attempt has a separate `ingestion_run`. Each run stores one `raw_object`
per distinct canonicalized payload, including rerun observations, so repeated
acquisition remains auditable. SHA-256 covers `payload_text` UTF-8 bytes: a
deterministic, lossless semantic JSON encoding (including Decimal numeric tokens),
not a claim to preserve original document whitespace or acquisition file bytes.
`storage_uri` identifies the database raw object. JSONB normalized snapshots use
decimal strings; authoritative raw JSON is text to avoid JSONB numeric limits.
`association_source.source_payload` contains that text and checksum.

Source links identify their first raw observation and run. Subsequent run raw
objects point to the same association. Composite foreign keys enforce that a
source link's raw object, run, association, and source record match, and that its
run belongs to the source. Quality issues similarly belong to the correct raw
object/run. Repeated identical records within one run are counted as duplicates.
Same-source concurrent writers are serialized with a PostgreSQL advisory lock.

The dataset transaction commits canonical data, raw evidence, findings, and
quarantine together. An unexpected failure rolls them back together, logs and
re-raises the exception, and records a failed run plus an ingestion-failure issue
in a separate audit transaction. The failed run contains no partially committed
dataset.

### Saved-fixture outcomes

One load of the 68 saved records produces 64 associations/source links: 55 loaded,
9 partial/unreconciled, and 4 quarantined multi-trait observations. It creates
1 variant, 25 traits, 63 studies, 47 trait synonyms, 68 raw observations, and
22 persisted quality issues. Two loads keep canonical counts unchanged and
produce 2 runs, 136 raw observations, and 44 issues.

Enrichment, complete public API contracts, and end-to-end protocol verification
remain future checkpoints. Persistence integration tests are not API completion.
