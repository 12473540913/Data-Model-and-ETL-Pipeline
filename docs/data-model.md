# Canonical Data Model

- **Variant**: canonical identity + preferred GRCh38 coordinate for milestone 1. Later add `variant_placement` for assembly-specific mappings.
- **VariantAlias**: rsID aliases, merged IDs, HGVS, SPDI, source-local IDs.
- **Trait**: normalized phenotype concept; retain source-reported names in `trait_synonym` / provenance.
- **Study**: accession/publication metadata.
- **Population**: normalized sample/ancestry descriptors while retaining source metadata.
- **Association**: statistical variant-trait observation in study/population context.
- **AssociationSource**: many-to-many provenance between canonical association and upstream source record/run.

Candidate later tables: `variant_placement`, `allele_frequency`, `ontology_term`, `ontology_edge`, `study_cohort`, `measurement_definition`, `summary_statistic_file`, `gene_annotation`, `publication`, `license`.
