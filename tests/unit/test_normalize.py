from genomic_platform.etl.normalize import ensembl_to_variant, normalize_rsid, parse_decimal

def test_normalize_rsid():
    assert normalize_rsid("RS00123") == "rs123"
    assert normalize_rsid(" rs12913832 ") == "rs12913832"
    assert normalize_rsid("abc") is None

def test_parse_decimal():
    assert str(parse_decimal("5e-8")) == "5E-8"
    assert parse_decimal("NA") is None

def test_ensembl_to_variant():
    payload={"var_class":"SNP","most_severe_consequence":"intron_variant","mappings":[{"assembly_name":"GRCh38","coord_system":"chromosome","seq_region_name":"15","start":28120472,"allele_string":"A/G","ancestral_allele":"A"}]}
    v=ensembl_to_variant("rs12913832",payload)
    assert (v.rsid,v.chromosome,v.position,v.reference_allele,v.alternate_alleles)==("rs12913832","15",28120472,"A",["G"])
