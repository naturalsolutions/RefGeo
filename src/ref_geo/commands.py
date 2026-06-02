from types import SimpleNamespace
import click
from flask.cli import with_appcontext
from sqlalchemy import func, select
import sqlalchemy as sa

from ref_geo.env import db
from ref_geo.models import BibAreasTypes, LAreas
from ref_geo.utils import (
    create_temporary_grids_table,
    insert_areas_from_temporary_table,
    insert_grids_from_temporary_table,
)
from utils_flask_sqla.utils import open_remote_file

BASE_URL = "https://geonature.fr/data/"


@click.group(help="Manage geographical referential.")
def ref_geo():
    pass


@ref_geo.command()
@with_appcontext
def info():
    click.echo("RefGeo - type de zonages")
    stmt = (
        select(
            BibAreasTypes,
            func.count(LAreas.id_area).label("count"),
            func.count(LAreas.id_area).filter(LAreas.enable.is_(True)).label("count_enabled"),
            func.min(LAreas.meta_create_date).label("create_date_min"),
            func.max(LAreas.meta_create_date).label("create_date_max"),
        )
        .join(LAreas)
        .group_by(BibAreasTypes.id_type)
        .order_by(BibAreasTypes.type_code)
    )
    fmt1 = "  {:5s} {:20s} {:>17s}    {:>10s}    {}"
    fmt2 = "  {:5s} {:20s} {:17d}    {:10d}    {:%Y-%m-%d} - {:%Y-%m-%d}"
    click.echo(
        fmt1.format(
            "code", "description", "nombre de zonages", "activés", "date de création des zonages"
        )
    )
    for area_type, count, count_enabled, create_date_min, create_date_max in db.session.execute(
        stmt
    ).all():
        click.echo(
            fmt2.format(
                area_type.type_code,
                area_type.type_name,
                count,
                count_enabled,
                create_date_min,
                create_date_max,
            )
        )


def compute_where_clause(
    *where_clauses,
    area_code=None,
    area_name=None,
    area_type_code=None,
    in_polygon=None,
    out_polygon=None,
    confirm=True,
):
    """
    Generate a filter to match a subset of areas of the geographical referential.
    Filters are cumulative (areas must match all filters).

    Parameters
    ----------
    *where_clauses: list of where clauses
        List of additionnal where clauses
    area_code : list of str
        List of area codes to match.
    area_name : list of str
        List of area names to match.
    area_type_code : list of str
        List of area types to activate or deactivate. The type codes are
        checked in the `bib_areas_types` table.
    in_polygon : str
        WKT polygon defined in WGS84 coordinate reference system. The
        areas inside the polygon will match.
    out_polygon : str
        WKT polygon defined in WGS84 coordinate reference system. The
        areas outside the polygon will match.
    confirm : bool
        If True, a summary of matched obs will be displayed and the user is asked to continue.
    """

    where_clauses = list(where_clauses)
    if area_code:
        where_clauses += [LAreas.area_code.in_(area_code)]
    if area_name:
        where_clauses += [LAreas.area_name.in_(area_name)]
    if area_type_code:
        where_clauses += [LAreas.area_type.has(BibAreasTypes.type_code.in_(area_type_code))]
    if in_polygon:
        where_clauses += [
            func.ST_Intersects(LAreas.geom_4326, func.ST_GeomFromText(in_polygon, 4326))
        ]
    if out_polygon:
        where_clauses += [
            sa.not_(func.ST_Intersects(LAreas.geom_4326, func.ST_GeomFromText(out_polygon, 4326)))
        ]
    if not where_clauses:
        raise click.UsageError("No filters provided!")

    where_clause = sa.and_(*where_clauses)

    if confirm:
        stmt = (
            select(
                BibAreasTypes,
                func.count(LAreas.id_area).label("count"),
            )
            .join(
                LAreas,
                sa.and_(BibAreasTypes.id_type == LAreas.id_type, where_clause),
            )
            .group_by(BibAreasTypes.id_type)
            .order_by(BibAreasTypes.type_code)
        )
        click.echo("Your filters matched this number of areas:")
        for area_type, count in db.session.execute(stmt).all():
            click.echo(f"  {area_type.type_code:5s} {count}")
        click.confirm("Continue?", abort=True)

    return where_clause


def change_area_activation_status(where_clause, enable):
    rowcount = db.session.execute(
        sa.update(LAreas)
        .where(where_clause)
        .where(LAreas.enable != enable)
        .values(enable=enable)
        .execution_options(synchronize_session=False)
    ).rowcount
    click.echo(f"{rowcount} areas have been {'activated' if enable else 'deactivated'}")
    db.session.commit()


@ref_geo.command(help="Deactivate geographical data")
@click.option("--area-code", "-a", multiple=True, help="Areas' code to deactivate")
@click.option("--area-name", "-n", multiple=True, help="Areas' name to deactivate")
@click.option(
    "--area-type-code",
    "-t",
    multiple=True,
    help="Areas’ type to deactivate",
)
@click.option(
    "--in-polygon",
    "-p",
    help="Indicate a polygon in which areas will be deactivated. Must be in WKT format (SRID 4326)",
)
@click.option(
    "--out-polygon",
    "-o",
    help="Indicate a polygon in which areas will be kept. Must be in WKT format (SRID 4326)",
)
@with_appcontext
def deactivate(**kwargs):
    click.echo("RefGeo : deactivating areas...")
    change_area_activation_status(compute_where_clause(**kwargs), False)


@ref_geo.command(help="Activate geographical data")
@click.option("--area-code", "-a", multiple=True, help="Areas' code to activate")
@click.option("--area-name", "-n", multiple=True, help="Areas' name to activate")
@click.option(
    "--area-type-code",
    "-t",
    multiple=True,
    help="Area type to activate (check `type_code` in `bib_areas_types` table)",
)
@click.option(
    "--in-polygon",
    "-i",
    help="Indicate a polygon in which areas will be activated. Must be in WKT format (SRID 4326)",
)
@click.option(
    "--out-polygon",
    "-o",
    help="Indicate a polygon in which areas will be kept. Must be in WKT format (SRID 4326)",
)
@with_appcontext
def activate(**kwargs):
    click.echo("RefGeo : activating areas...")
    change_area_activation_status(compute_where_clause(**kwargs), True)


@ref_geo.command(help="Delete geographical data")
@click.option("--area-code", "-a", multiple=True, help="Areas' code to delete")
@click.option("--area-name", "-n", multiple=True, help="Areas' name to delete")
@click.option("--area-type-code", "-t", multiple=True, help="Areas' type to delete")
@click.option(
    "--in-polygon",
    "-i",
    help="Indicate a polygon in which areas will be deleted. Must be in WKT format (SRID 4326)",
)
@click.option(
    "--out-polygon",
    "-o",
    help="Indicate a polygon in which areas will be kept. Must be in WKT format (SRID 4326)",
)
@with_appcontext
def delete(**kwargs):

    rowcount = db.session.execute(
        sa.delete(LAreas)
        .where(compute_where_clause(**kwargs))
        .execution_options(synchronize_session=False)
    ).rowcount
    click.confirm(f"{rowcount} areas have been deleted, commit?", abort=True)
    db.session.commit()


@ref_geo.group(name="import", help="Import geographical data")
def ref_geo_import():
    pass


GRIDS = {
    "m1": {
        "filename": "inpn_grids_1.csv.xz",
        "temp_table_name": "temp_grids_1",
        "area_type": "M1",
        "versions": ["2020"],
    },
    "m2": {
        "filename": "inpn_grids_2.csv.xz",
        "temp_table_name": "temp_grids_2",
        "area_type": "M2",
        "versions": ["2024"],
    },
    "m5": {
        "filename": "inpn_grids_5.csv.xz",
        "temp_table_name": "temp_grids_5",
        "area_type": "M5",
        "versions": ["2020"],
    },
    "m10": {
        "filename": "inpn_grids_10.csv.xz",
        "temp_table_name": "temp_grids_10",
        "area_type": "M10",
        "versions": ["2020"],
    },
    "m20": {
        "filename": "inpn_grids_20.csv.xz",
        "temp_table_name": "temp_grids_20",
        "area_type": "M20",
        "versions": ["2024"],
    },
    "m50": {
        "filename": "inpn_grids_50.csv.xz",
        "temp_table_name": "temp_grids_50",
        "area_type": "M50",
        "versions": ["2024"],
    },
}


@ref_geo_import.command()
@click.option(
    "--kind",
    type=click.Choice(["m1", "m2", "m5", "m10", "m20", "m50"], case_sensitive=False),
    required=True,
)
@click.option("--version")
@click.option("--enable/--disable", default=True)
@click.option("--base-url", default=f"{BASE_URL}inpn/layers/")
@with_appcontext
def inpn_grids(kind, version, enable, base_url):
    schema = "ref_geo"
    kind = SimpleNamespace(**GRIDS[kind])
    if version is None:
        version = sorted(kind.versions)[-1]
        click.echo(f"Selecting version '{version}'")
    if version not in kind.versions:
        raise click.BadParameter(
            f"This referential exists only in following versions: {kind.versions}"
        )
    base_url = f"{base_url}{version}/"

    grids_count = db.session.execute(
        sa.select(sa.func.count(LAreas.id_area)).where(
            LAreas.area_type.has(BibAreasTypes.type_code == kind.area_type)
        )
    ).scalar()
    if grids_count:
        click.confirm(
            f"There are already {grids_count} existing grids, are you sure you want to continue?",
            abort=True,
        )

    click.echo("Create temporary grids table…")
    create_temporary_grids_table(db.session, schema, kind.temp_table_name)
    cursor = db.session.connection().connection.cursor()
    with open_remote_file(base_url, kind.filename) as geofile:
        click.echo("Inserting grids data in temporary table…")
        cursor.copy_expert(f"COPY {schema}.{kind.temp_table_name} FROM STDIN", geofile)
    click.echo("Copy grids in l_areas…")
    insert_areas_from_temporary_table(
        db.session, schema, kind.temp_table_name, kind.area_type, enable
    )
    insert_grids_from_temporary_table(db.session, schema, kind.temp_table_name)
    click.echo("Dropping temporary grids table…")
    db.session.execute(f"DROP TABLE {schema}.{kind.temp_table_name}")
    click.echo("Committing…")
    db.session.commit()


@ref_geo_import.command()
@click.option("--version", default="2026-05")
@click.option("--enable/--disable", default=True)
@click.option("--base-url", default=f"{BASE_URL}ign/")
@with_appcontext
def fr_epci(version, enable, base_url):
    schema = "ref_geo"
    filename = f"epci_fr_{version}.csv.xz"
    temp_table_name = "temp_fr_epci"
    click.echo("Ensure EPCI type exists in bib_areas_types…")
    epci_type = db.session.execute(
        select(BibAreasTypes).where(BibAreasTypes.type_code == "EPCI")
    ).scalar_one_or_none()
    if not epci_type:
        with db.session.begin_nested():
            epci_type = BibAreasTypes(
                type_code="EPCI",
                type_name="EPCI",
                type_desc="Établissement public de coopération intercommunale",
                size_hierarchy=25000,
            )
            db.session.add(epci_type)
        click.echo(f"EPCI type created (id_type={epci_type.id_type})")

    epci_count = db.session.execute(
        sa.select(sa.func.count(LAreas.id_area)).where(LAreas.id_type == epci_type.id_type)
    ).scalar()
    if epci_count:
        click.confirm(
            f"There are already {epci_count} existing EPCI, are you sure you want to continue?",
            abort=True,
        )

    click.echo("Update EPCI type referential name & version")
    with db.session.begin_nested():
        epci_type.ref_name = "IGN admin_express"
        epci_type.num_version = version

    click.echo("Create temporary EPCI table…")
    db.session.execute(f"""
        CREATE TABLE {schema}.{temp_table_name} (
            WKT public.geometry(MultiPolygon,4326),
            fid integer NOT NULL,
            cleabs text,
            nom_officiel text,
            nom_officiel_en_majuscules text,
            nature text,
            codes_insee_des_communes_membres text,
            codes_insee_des_departements_membres text,
            code_siren text
        )
    """)
    db.session.execute(f"""
        ALTER TABLE ONLY {schema}.{temp_table_name}
            ADD CONSTRAINT {temp_table_name}_pkey PRIMARY KEY (fid)
    """)
    with open_remote_file(base_url, filename) as csvfile:
        click.echo("Inserting EPCI data in temporary table…")
        db.session.connection().connection.cursor().copy_expert(
            f"COPY {schema}.{temp_table_name} FROM STDIN DELIMITER ',' CSV HEADER", csvfile
        )
    click.echo(f"Insert EPCI in l_areas…")
    rowcount = db.session.execute(f"""
        INSERT INTO {schema}.l_areas (id_type, area_code, area_name, geom, geom_4326, enable)
        SELECT
            {epci_type.id_type},
            code_siren,
            nom_officiel,
            ST_Transform(WKT, Find_SRID('{schema}', 'l_areas', 'geom')),
            ST_Transform(WKT, 4326),
            {str(enable).upper()}
        FROM {schema}.{temp_table_name}
    """).rowcount
    click.echo("Re-indexing…")
    # TODO: may be use codes_insee_des_communes_membres & codes_insee_des_departements_membres to populate cor_areas
    db.session.execute(f"REINDEX INDEX {schema}.index_l_areas_geom")
    click.echo("Dropping temporary EPCI table…")
    db.session.execute(f"DROP TABLE {schema}.{temp_table_name}")
    click.echo("Committing…")
    db.session.commit()
