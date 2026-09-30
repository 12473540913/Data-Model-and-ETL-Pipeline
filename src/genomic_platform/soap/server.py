from wsgiref.simple_server import make_server
from spyne import Application, ComplexModel, Integer, Iterable, ServiceBase, Unicode, rpc
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication
from genomic_platform.db import SessionLocal
from genomic_platform.repositories.catalog import CatalogRepository
from genomic_platform.services.catalog import CatalogService

class VariantSoap(ComplexModel):
    rsid=Unicode; assembly=Unicode; chromosome=Unicode; position=Integer; reference_allele=Unicode

class GenomicCatalogSoapService(ServiceBase):
    @rpc(Unicode,_returns=VariantSoap)
    def GetVariant(ctx,rsid):
        with SessionLocal() as s:
            v=CatalogService(CatalogRepository(s)).get_variant(rsid)
            return VariantSoap() if not v else VariantSoap(rsid=v.rsid,assembly=v.assembly,chromosome=v.chromosome,position=v.position,reference_allele=v.reference_allele)
    @rpc(Unicode,Integer,_returns=Iterable(Unicode))
    def GetTraitAssociationIds(ctx,trait_name,limit=100):
        with SessionLocal() as s:
            for row in CatalogService(CatalogRepository(s)).get_trait_associations(trait_name,limit): yield str(row.id)

application=Application([GenomicCatalogSoapService],tns="genomic.platform.catalog",in_protocol=Soap11(validator="lxml"),out_protocol=Soap11())
if __name__ == "__main__":
    server=make_server("0.0.0.0",8001,WsgiApplication(application)); print("SOAP/WSDL: http://localhost:8001/?wsdl"); server.serve_forever()
