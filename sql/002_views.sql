CREATE OR REPLACE VIEW v_trait_association AS
SELECT a.id association_id,t.name trait_name,t.ontology_id,v.rsid,v.assembly,v.chromosome,v.position,s.accession study_accession,s.pubmed_id,a.p_value,a.effect_allele,a.other_allele,a.effect_size,a.effect_size_type
FROM association a
JOIN trait t ON t.id=a.trait_id
JOIN variant v ON v.id=a.variant_id
LEFT JOIN study s ON s.id=a.study_id;
