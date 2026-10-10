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

### Required schema work before loading (not implemented here)

The existing `association.p_value NUMERIC(50, 40)` cannot represent values such as
`1e-65`; it would round them to zero. Replace it through an Alembic migration with
unconstrained PostgreSQL `NUMERIC` and retain the source scientific representation
in a text column or provenance metadata. PostgreSQL numeric storage still has
finite exponent limits, so reject out-of-range values explicitly and preserve
their source text. Also assess the fixed scale on `effect_size` before loading.
Database loading, migrations, enrichment, and protocol integration are later
checkpoints; normalization tests do not establish end-to-end ETL completion.
