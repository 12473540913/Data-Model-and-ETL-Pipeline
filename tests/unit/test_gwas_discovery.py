import asyncio

from genomic_platform.connectors.gwas_catalog import GWASCatalogConnector


def test_association_discovery_filters_and_preserves_paginated_records():
    async def run_discovery(method_name, identifier, expected_filter):
        connector = GWASCatalogConnector()
        first_record = {"id": "association-1", "sourceSpecific": {"value": 12}}
        second_record = {"id": "association-2", "pValueMantissa": -8}
        calls = []
        pages = [
            {"content": [first_record], "_links": {"next": {"href": "/associations?page=2"}}},
            {"results": [second_record]},
        ]

        async def fake_get(path, params=None):
            calls.append((path, params))
            return type(
                "Response", (), {"json": lambda self, **kwargs: pages.pop(0), "status_code": 200}
            )()

        connector.http.get = fake_get
        iterator = getattr(connector, method_name)(identifier, page_size=17)
        records = [record async for record in iterator]

        assert records == [first_record, second_record]
        assert calls == [
            ("/associations", {**expected_filter, "size": 17}),
            ("/associations?page=2", None),
        ]

    asyncio.run(
        run_discovery(
            "iter_associations_for_trait",
            "EFO_0000001",
            {"efo_trait": "EFO_0000001", "show_child_traits": "true"},
        )
    )
    asyncio.run(
        run_discovery(
            "iter_associations_for_variant",
            "rs123",
            {"rs_id": "rs123"},
        )
    )
