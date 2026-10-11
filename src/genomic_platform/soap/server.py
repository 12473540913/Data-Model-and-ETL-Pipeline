import io
import logging
from datetime import datetime
from types import UnionType
from typing import get_args, get_origin
from wsgiref.simple_server import make_server

from lxml import etree
from pydantic import BaseModel
from spyne import (
    Application,
    Array,
    Boolean,
    ComplexModel,
    DateTime,
    Fault,
    Integer64,
    ServiceBase,
    Unicode,
    rpc,
)
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication
from sqlalchemy.exc import SQLAlchemyError

from genomic_platform.api.deps import build_catalog_service, read_session
from genomic_platform.db import SessionLocal
from genomic_platform.services import contracts as c
from genomic_platform.services.catalog import QueryError

logger = logging.getLogger(__name__)
NAMESPACE = "genomic.platform.catalog"
SOAP_TYPES = {}


def soap_type(annotation):
    if get_origin(annotation) is UnionType:
        return soap_type(next(item for item in get_args(annotation) if item is not type(None)))
    if get_origin(annotation) is list:
        return Array(soap_type(get_args(annotation)[0]))
    if annotation in SOAP_TYPES:
        return SOAP_TYPES[annotation]
    return {str: Unicode, int: Integer64, bool: Boolean, datetime: DateTime}[annotation]


for contract in c.CONTRACTS:
    SOAP_TYPES[contract] = type(
        contract.__name__,
        (ComplexModel,),
        {
            "__namespace__": NAMESPACE,
            **{name: soap_type(field.annotation) for name, field in contract.model_fields.items()},
        },
    )


def to_soap(value):
    if isinstance(value, BaseModel):
        return SOAP_TYPES[type(value)](
            **{key: to_soap(getattr(value, key)) for key in type(value).model_fields}
        )
    if isinstance(value, list):
        return [to_soap(item) for item in value]
    return value


class CatalogFault(Fault):
    __namespace__ = NAMESPACE


def call(ctx, operation, **kwargs):
    for name, default in (("limit", 50), ("offset", 0)):
        if name in kwargs and kwargs[name] is None:
            kwargs[name] = default
    try:
        with read_session(ctx.app.catalog_session_factory) as session:
            return to_soap(build_catalog_service(session).execute(operation, **kwargs))
    except QueryError as error:
        raise CatalogFault(
            faultcode=(
                "Server." if error.code in ("DATABASE_UNAVAILABLE", "INTERNAL_ERROR") else "Client."
            )
            + error.code,
            faultstring=str(error),
        ) from None
    except SQLAlchemyError as error:
        logger.error("SOAP database unavailable (%s).", type(error).__name__)
        raise CatalogFault(
            faultcode="Server.DATABASE_UNAVAILABLE", faultstring="Database query unavailable."
        ) from None


class GenomicCatalogSoapService(ServiceBase):
    @rpc(Unicode, _returns=SOAP_TYPES[c.VariantView], _faults=[CatalogFault])
    def GetVariant(ctx, rsid):
        return call(ctx, "get_variant", rsid=rsid)

    @rpc(Unicode, _returns=SOAP_TYPES[c.TraitView], _faults=[CatalogFault])
    def GetTrait(ctx, ontology_id):
        return call(ctx, "get_trait", ontology_id=ontology_id)

    @rpc(Unicode, _returns=SOAP_TYPES[c.GeneView], _faults=[CatalogFault])
    def GetGene(ctx, symbol):
        return call(ctx, "get_gene", symbol=symbol)

    @rpc(Unicode, _returns=SOAP_TYPES[c.StudyView], _faults=[CatalogFault])
    def GetStudy(ctx, study_id):
        return call(ctx, "get_study", study_id=study_id)

    @rpc(Unicode, _returns=SOAP_TYPES[c.AssociationView], _faults=[CatalogFault])
    def GetAssociation(ctx, association_id):
        return call(ctx, "get_association", association_id=association_id)

    @rpc(
        Unicode,
        Integer64,
        Integer64,
        _returns=SOAP_TYPES[c.AssociationPage],
        _faults=[CatalogFault],
    )
    def GetVariantAssociations(ctx, rsid, limit=50, offset=0):
        return call(ctx, "variant_associations", rsid=rsid, limit=limit, offset=offset)

    @rpc(
        Unicode,
        Integer64,
        Integer64,
        _returns=SOAP_TYPES[c.AssociationPage],
        _faults=[CatalogFault],
    )
    def GetTraitAssociations(ctx, ontology_id, limit=50, offset=0):
        return call(ctx, "trait_associations", ontology_id=ontology_id, limit=limit, offset=offset)

    @rpc(
        Unicode,
        Integer64,
        Integer64,
        _returns=SOAP_TYPES[c.AssociationPage],
        _faults=[CatalogFault],
    )
    def GetGeneAssociations(ctx, symbol, limit=50, offset=0):
        return call(ctx, "gene_associations", symbol=symbol, limit=limit, offset=offset)

    @rpc(Unicode, Integer64, _returns=Array(Unicode), _faults=[CatalogFault])
    def GetTraitAssociationIds(ctx, trait_name, limit=50):
        result = call(ctx, "get_trait_associations", trait_name=trait_name, limit=limit)
        return [row.id for row in result.items]

    @rpc(Unicode, Integer64, Integer64, _returns=SOAP_TYPES[c.SourcePage], _faults=[CatalogFault])
    def GetAssociationSources(ctx, association_id, limit=50, offset=0):
        return call(
            ctx, "association_sources", association_id=association_id, limit=limit, offset=offset
        )

    @rpc(
        Unicode,
        Integer64,
        Integer64,
        Unicode,
        _returns=SOAP_TYPES[c.MappingPage],
        _faults=[CatalogFault],
    )
    def GetVariantMappings(ctx, rsid, limit=50, offset=0, assembly=None):
        return call(
            ctx, "variant_mappings", rsid=rsid, limit=limit, offset=offset, assembly=assembly
        )

    @rpc(
        Unicode, Integer64, Integer64, _returns=SOAP_TYPES[c.EnrichmentPage], _faults=[CatalogFault]
    )
    def GetVariantEnrichments(ctx, rsid, limit=50, offset=0):
        return call(ctx, "variant_enrichments", rsid=rsid, limit=limit, offset=offset)

    @rpc(Unicode, _returns=SOAP_TYPES[c.RawView], _faults=[CatalogFault])
    def GetRawEvidence(ctx, raw_id):
        return call(ctx, "get_raw", raw_id=raw_id)

    @rpc(Unicode, _returns=SOAP_TYPES[c.RunView], _faults=[CatalogFault])
    def GetIngestionRun(ctx, run_id):
        return call(ctx, "get_run", run_id=run_id)

    @rpc(
        Unicode,
        Integer64,
        Integer64,
        Unicode,
        _returns=SOAP_TYPES[c.RawPage],
        _faults=[CatalogFault],
    )
    def GetRunRaw(ctx, run_id, limit=50, offset=0, disposition=None):
        return call(
            ctx, "run_raw", run_id=run_id, limit=limit, offset=offset, disposition=disposition
        )

    @rpc(
        Unicode, Integer64, Integer64, _returns=SOAP_TYPES[c.ProcessingPage], _faults=[CatalogFault]
    )
    def GetRunProcessing(ctx, run_id, limit=50, offset=0):
        return call(ctx, "run_processing", run_id=run_id, limit=limit, offset=offset)

    @rpc(
        Unicode,
        Unicode,
        Integer64,
        Integer64,
        _returns=SOAP_TYPES[c.QualityPage],
        _faults=[CatalogFault],
    )
    def GetQuality(ctx, scope, identifier, limit=50, offset=0):
        return call(ctx, "quality", scope=scope, identifier=identifier, limit=limit, offset=offset)


def soap_fault(start_response, code, message, status="500 Internal Server Error"):
    envelope = etree.Element(
        "{http://schemas.xmlsoap.org/soap/envelope/}Envelope",
        nsmap={"soap": "http://schemas.xmlsoap.org/soap/envelope/"},
    )
    body = etree.SubElement(envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
    fault = etree.SubElement(body, "{http://schemas.xmlsoap.org/soap/envelope/}Fault")
    etree.SubElement(fault, "faultcode").text = code
    etree.SubElement(fault, "faultstring").text = message
    payload = etree.tostring(envelope, xml_declaration=True, encoding="utf-8")
    start_response(
        status, [("Content-Type", "text/xml; charset=utf-8"), ("Content-Length", str(len(payload)))]
    )
    return [payload]


def create_soap_app(session_factory=SessionLocal):
    application = Application(
        [GenomicCatalogSoapService],
        tns=NAMESPACE,
        in_protocol=Soap11(
            validator="lxml", resolve_entities=False, load_dtd=False, no_network=True
        ),
        out_protocol=Soap11(),
    )
    application.catalog_session_factory = session_factory
    wsgi = WsgiApplication(application)

    def guarded(environ, start_response):
        method = environ.get("REQUEST_METHOD")
        if method == "GET":
            return wsgi(environ, start_response)
        if method != "POST":
            return soap_fault(
                start_response,
                "Client.INVALID_INPUT",
                "Only read-only SOAP operations are supported.",
                "405 Method Not Allowed",
            )
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError:
            return soap_fault(start_response, "Client.INVALID_INPUT", "Invalid content length.")
        if not 0 < length <= 65536:
            return soap_fault(
                start_response,
                "Client.QUERY_LIMIT",
                "SOAP request must be 1-65536 bytes.",
                "413 Payload Too Large",
            )
        payload = environ["wsgi.input"].read(length)
        try:
            document = etree.fromstring(
                payload,
                parser=etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True),
            )
            if document.getroottree().docinfo.doctype:
                return soap_fault(
                    start_response, "Client.INVALID_INPUT", "DTD declarations are prohibited."
                )
        except etree.XMLSyntaxError:
            return soap_fault(start_response, "Client.INVALID_INPUT", "Malformed SOAP XML.")
        environ["wsgi.input"] = io.BytesIO(payload)
        return wsgi(environ, start_response)

    guarded.application = application
    return guarded


wsgi_application = create_soap_app()
application = wsgi_application.application


if __name__ == "__main__":
    with make_server("127.0.0.1", 8001, wsgi_application) as server:
        print("SOAP/WSDL: http://localhost:8001/?wsdl")
        server.serve_forever()
