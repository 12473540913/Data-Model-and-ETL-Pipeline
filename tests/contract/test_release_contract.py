import re
import tomllib
from pathlib import Path

from alembic.script import ScriptDirectory
from graphql import parse, validate
from lxml import etree

from genomic_platform import __version__
from genomic_platform.api.graphql_schema import schema
from genomic_platform.api.main import app
from genomic_platform.schema_migrations import migration_config
from genomic_platform.soap.server import NAMESPACE, GenomicCatalogSoapService

ROOT = Path(__file__).resolve().parents[2]


def test_migration_configuration_uses_portable_paths():
    config = migration_config()
    assert config.get_main_option("script_location").endswith("/migrations")
    assert Path(ScriptDirectory.from_config(config).dir).is_dir()


def test_release_versions_agree():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["version"] == __version__ == app.version


def test_documented_graphql_examples_match_schema():
    examples = re.findall(
        r"```graphql\n(.*?)```", (ROOT / "docs" / "api.md").read_text(), re.DOTALL
    )
    assert examples
    for example in examples:
        assert validate(schema._schema, parse(example)) == []


def test_documented_soap_example_names_are_declared():
    examples = re.findall(r"```xml\n(.*?)```", (ROOT / "docs" / "api.md").read_text(), re.DOTALL)
    assert examples
    for example in examples:
        envelope = etree.fromstring(example.encode())
        operation = envelope.find("{http://schemas.xmlsoap.org/soap/envelope/}Body")[0]
        assert etree.QName(operation).namespace == NAMESPACE
        descriptor = GenomicCatalogSoapService.public_methods[etree.QName(operation).localname]
        assert [etree.QName(child).localname for child in operation] == list(
            descriptor.in_message._type_info
        )
