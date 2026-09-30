from genomic_platform.etl.normalize import gwas_record_to_association

def test_gwas_transformer_accepts_sample_shape():
    record={"id":"assoc-1","rsId":"rs12913832","efoTrait":"eye color","pValue":"1e-20","studyAccession":"GCST-DEMO"}
    assert gwas_record_to_association(record).variant_rsid == "rs12913832"
