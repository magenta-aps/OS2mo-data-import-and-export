# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Program to fetch data from an actualstate sqlitedatabase, written for creating
#  excel-reports with XLSXExporte.py
# See customers/Frederikshavn/Frederikshavn_reports.py for an example
import csv
from operator import itemgetter
from typing import Dict
from typing import List

import jmespath
import numpy as np
import pandas as pd
import xlsxwriter.worksheet
from fastramqpi.raclients.graph.client import GraphQLClient
from gql import gql
from more_itertools import prepend
from pydantic import BaseSettings
from sqlalchemy import or_
from sqlalchemy.orm import sessionmaker

from exporters.sql_export.lc_for_jobs_db import get_engine
from exporters.sql_export.sql_table_defs import WAdresse as Adresse
from exporters.sql_export.sql_table_defs import WBruger as Bruger
from exporters.sql_export.sql_table_defs import WEngagement as Engagement
from exporters.sql_export.sql_table_defs import WEnhed as Enhed
from exporters.sql_export.sql_table_defs import WTilknytning as Tilknytning


class XLSXExporter:
    """Exporter for writing xlsx files with autofilters and columnwidts ajusted to its
    content.

    Accepts data in lists of lists where first lists contains the title of the columns,
    eg:
    [["Navn", "Email", "Tilknytningstype", "Enhed"]
    ["Fornavn Efternavn", "email@example.com", "Formand", "Enhed"]]
    """

    def __init__(self, xlsx_file: str):
        self.xlsx_file = xlsx_file

    @staticmethod
    def write_rows(worksheet: xlsxwriter.worksheet.Worksheet, data: list):
        for index, row in enumerate(data):
            worksheet.write_row(index, 0, row)

    @staticmethod
    def get_column_width(data, field: int):
        data = filter(itemgetter(field), data)
        try:
            field_length = max(len(row[field]) for row in data)
            return field_length
        except IndexError:
            return 10

    def add_sheet(self, workbook, sheet: str, data: list):
        worksheet = workbook.add_worksheet(name=sheet)
        worksheet.autofilter(0, 0, len(data), len(data[0]) - 1)

        for index in range(len(data[0])):
            worksheet.set_column(
                index,
                index,
                width=self.get_column_width(data, index),
            )

        bold = workbook.add_format({"bold": 1})
        worksheet.set_row(0, cell_format=bold)

        self.write_rows(worksheet, data)


def expand_org_path(df: pd.DataFrame, path_col: str) -> pd.DataFrame:
    # Create new dataframe with organisational path as columns
    org_paths = df[path_col].str.split("\\", expand=True)
    new_cols = [f"Enhed {column + 1}" for column in org_paths.columns]
    org_paths.columns = new_cols

    # Remove the path column and join org_paths instead
    df = df.drop(columns=path_col)
    return df.join(org_paths)


def set_of_org_units(session, org_name: str) -> set:
    """Find all uuids of org_units under the organisation  :code:`org_name`."""
    query_result = (
        session.query(Enhed.uuid).filter(Enhed.navn == org_name).one_or_none()
    )

    if query_result is None:
        raise ValueError(f'No organisation unit was found with name: "{org_name}"')

    else:
        hoved_enhed = query_result[0]

    # Find all children of the unit and collect in a set
    def find_children(enheder):
        """Return a set of children under :code:`enheder`."""
        under_enheder = (
            session.query(Enhed.uuid)
            .filter(Enhed.forældreenhed_uuid.in_(enheder))
            .all()
        )
        # query returns a list of tuples like [(uuid2,),(uuid2,)],
        # so extract the first item in each.
        return set(enheder[0] for enheder in under_enheder)

    under_enheder = find_children(set([hoved_enhed]))
    alle_enheder = under_enheder
    # Update the set with any new units
    while under_enheder:
        under_enheder = find_children(under_enheder)
        alle_enheder.update(under_enheder)

    return alle_enheder


class Settings(BaseSettings):
    mora_base: str = "http://localhost:5000"
    client_id: str = "dipex"
    client_secret: str
    auth_realm: str = "mo"
    auth_server: str = "http://localhost:5000/auth"


def map_trade_union(result: list) -> Dict[str, str]:
    """Transforms a list of associations from graphql into a dict.
    Uses jmes to search the results for names of classes parent classes.
    If there is a parent output as "parent name / class name"
    """
    dynamic_classes = jmespath.compile("current.trade_union.name")
    dynamic_class_parents = jmespath.compile("current.trade_union.parent.name")
    return {
        e["uuid"]: (
            f"{dynamic_class_parents.search(e)} / {dynamic_classes.search(e)}"
            if dynamic_class_parents.search(e)
            else dynamic_classes.search(e)
        )
        for e in result
    }


def fetch_trade_union(association_uuids: List[str]) -> Dict[str, str]:
    """Reads dynamic class for the associations with uuids from the given list

    Returns a map of association_uuids to dynamic class name
    including parent class if there is one.

    """

    settings = Settings()  # type: ignore
    query = gql(
        """
        query employeeDynamicClasses($uuids: [UUID!]) {
          associations(filter: { uuids: $uuids }) {
            objects {
              uuid
              current {
                trade_union {
                  name
                  parent {
                    name
                  }
                }
              }
            }
          }
        }
        """
    )

    with GraphQLClient(
        url=f"{settings.mora_base}/graphql/v22",
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        auth_realm=settings.auth_realm,
        auth_server=settings.auth_server,  # type: ignore
        sync=True,
        httpx_client_kwargs={"timeout": None},
    ) as session:
        r = session.execute(
            query,
            variable_values={
                # UUIDs are not JSON serializable, so they are converted to strings
                "uuids": association_uuids,
            },
        )

    return map_trade_union(r["associations"]["objects"])


def merge_trade_unions(
    data_df: pd.DataFrame, association_trade_unions: Dict[str, str]
) -> pd.DataFrame:
    """Merges information on dynamic classes into the dataframe.
    This creates a new column on the given dataframe data_df called "Hovedorganisation / Faglig organisation".
    It is merged with a left join between the dataframe and the given association_dynamic_classes mapping using
    the association uuid as key. This ensures the information is added where relevant without altering the rest.
    """
    # Create a new pandas dataframe with uuid on associations and their dynamic class.
    association_df = pd.DataFrame(
        association_trade_unions.items(),
        columns=["Tilknytningsuuid", "Hovedorganisation / Faglig organisation"],
    )
    # Merge (left join) on uuids.
    data_df = data_df.merge(association_df, on="Tilknytningsuuid", how="left")

    return data_df.replace({np.nan: None})  # Replace nan values with None


def rearrange(data_df: pd.DataFrame) -> pd.DataFrame:
    """Rearranges the columns in the dataframe.
    The column "Tilknytningsuuid" is dropped as it is only used for joining data.
    "Hovedorganisation / Faglig organisation" is moved from the last column to 5th.
    """

    data_df = data_df.drop(columns="Tilknytningsuuid")
    columns = list(data_df.columns)
    columns.remove("Hovedorganisation / Faglig organisation")
    columns.insert(4, "Hovedorganisation / Faglig organisation")

    return data_df.reindex(columns, axis=1)


def list_MED_members(session, org_names: dict) -> list:
    """Lists all "tilknyntninger" to an organisation.

    Returns a list of tuples with titles as first element
    and data on members in subsequent tuples. Example:
    [
        ("Navn", "Email", "Tilknytningstype", "Enhed"),
        ("Fornavn Efternavn", "email@example.com", "Formand", "Enhed")
    ]
    """
    alle_enheder = set_of_org_units(session, org_names["løn"])
    alle_MED_enheder = set_of_org_units(session, org_names["MED"])
    Emails = (
        session.query(Adresse.værdi, Adresse.bruger_uuid)
        .filter(
            Adresse.adressetype_titel == "AD-Email",
            or_(
                Adresse.synlighed_titel.is_(None),
                Adresse.synlighed_titel != "Hemmelig",
            ),
        )
        .subquery()
    )
    Phonenr = (
        session.query(Adresse.værdi, Adresse.bruger_uuid)
        .filter(
            Adresse.adressetype_titel == "AD-Telefonnummer",
            or_(
                Adresse.synlighed_titel.is_(None),
                Adresse.synlighed_titel != "Hemmelig",
            ),
        )
        .subquery()
    )
    eng_unit = (
        session.query(
            Enhed.navn, Enhed.organisatorisk_sti, Engagement.bruger_uuid
        ).filter(
            Enhed.uuid == Engagement.enhed_uuid,
            Engagement.enhed_uuid.in_(alle_enheder),
            Engagement.bruger_uuid == Bruger.uuid,
        )
    ).subquery()

    query = (
        session.query(
            Tilknytning.uuid,
            Tilknytning.startdato,
            Tilknytning.slutdato,
            Bruger.fornavn + " " + Bruger.efternavn,
            Emails.c.værdi,
            Phonenr.c.værdi,
            Tilknytning.tilknytningstype_titel,
            Enhed.navn,
            eng_unit.c.navn,
            eng_unit.c.organisatorisk_sti,
        )
        .filter(
            Enhed.uuid == Tilknytning.enhed_uuid,
            Tilknytning.enhed_uuid.in_(alle_MED_enheder),
            Tilknytning.bruger_uuid == Bruger.uuid,
        )
        .join(Emails, Emails.c.bruger_uuid == Bruger.uuid, isouter=True)
        .join(Phonenr, Phonenr.c.bruger_uuid == Bruger.uuid, isouter=True)
        .join(eng_unit, eng_unit.c.bruger_uuid == Bruger.uuid)
        .order_by(Bruger.efternavn)
    )
    data = query.all()
    data_df = pd.DataFrame(
        data,
        columns=[
            "Tilknytningsuuid",
            "Tilknytningens startdato",
            "Tilknytningens slutdato",
            "Navn",
            "Email",
            "Telefonnummer",
            "Tilknytningstype",
            "Tilknytningsenhed",
            "Ansættelsesenhed",
            "Sti",
        ],
    )
    data_df = expand_org_path(data_df, "Sti")
    # Add dynamic class info:
    association_dynamic_class = fetch_trade_union(list(data_df.Tilknytningsuuid))

    data_df = merge_trade_unions(data_df, association_dynamic_class)

    data_df = rearrange(data_df)
    data_df.drop_duplicates(inplace=True)

    # Return data as a list of tuples with columns as the first element
    parsed_data = list(prepend(data_df.columns, data_df.to_records(index=False)))

    return parsed_data


def list_org_units(session, org_name: str) -> list:
    query = session.query(Enhed.bvn, Enhed.organisatorisk_sti)
    data = query.all()
    data_df = pd.DataFrame(data, columns=["Enhedsnr", "Sti"])
    data_df = expand_org_path(data_df, "Sti")
    # Return data as a list of tuples with columns as the first element
    parsed_data = list(prepend(data_df.columns, data_df.to_records(index=False)))
    return parsed_data


def list_employees(session, org_name: str) -> list:
    """Lists all employees in organisation.

    Returns a list of tuples with titles as first element
    and data on employees in subsequent tuples. Example:
    [
        (Navn", "CPR", "Email", "Telefon", "Enhed", "Stilling"),
        ("Fornavn Efternavn", 0123456789,  "email@example.com", "12345678",
            "Enhedsnavn", "Stillingsbetegnelse")
    ]
    """
    alle_enheder = set_of_org_units(session, org_name)

    Emails = (
        session.query(Adresse.værdi, Adresse.bruger_uuid)
        .filter(
            Adresse.adressetype_titel == "AD-Email",
            or_(
                Adresse.synlighed_titel.is_(None),
                Adresse.synlighed_titel != "Hemmelig",
            ),
        )
        .subquery()
    )
    Phonenr = (
        session.query(Adresse.værdi, Adresse.bruger_uuid)
        .filter(
            Adresse.adressetype_titel == "AD-Telefonnummer",
            or_(
                Adresse.synlighed_titel.is_(None),
                Adresse.synlighed_titel != "Hemmelig",
            ),
        )
        .subquery()
    )
    query = (
        session.query(
            Bruger.uuid,
            Bruger.fornavn + " " + Bruger.efternavn,
            Bruger.cpr,
            Emails.c.værdi,
            Phonenr.c.værdi,
            Enhed.navn,
            Enhed.organisatorisk_sti,
            Engagement.stillingsbetegnelse_titel,
            Engagement.bvn,
        )
        .filter(
            Enhed.uuid == Engagement.enhed_uuid,
            Engagement.enhed_uuid.in_(alle_enheder),
            Engagement.bruger_uuid == Bruger.uuid,
        )
        .join(Emails, Emails.c.bruger_uuid == Bruger.uuid, isouter=True)
        .join(Phonenr, Phonenr.c.bruger_uuid == Bruger.uuid, isouter=True)
        .order_by(Bruger.efternavn)
    )
    data = query.all()
    data_df = pd.DataFrame(
        data,
        columns=[
            "UUID",
            "Navn",
            "CPR",
            "AD-Email",
            "AD-Telefonnummer",
            "Enhed",
            "Sti",
            "Stilling",
            "MA-nummer",
        ],
    )
    data_df = expand_org_path(data_df, "Sti")
    data_df.drop_duplicates(inplace=True)

    # Return data as a list of tuples with columns as the first element
    parsed_data = list(prepend(data_df.columns, data_df.to_records(index=False)))
    return parsed_data


def run_report(reporttype, sheetname: str, org_name: str, xlsx_file: str):
    # Make a sqlalchemy session - Name of database is read from settings
    session = sessionmaker(bind=get_engine(), autoflush=False)()

    # Make the query
    data = reporttype(session, org_name)

    # write data as excel file
    workbook = xlsxwriter.Workbook(xlsx_file)
    excel = XLSXExporter(xlsx_file)
    excel.add_sheet(workbook, sheetname, data)
    workbook.close()


def run_report_as_csv(reporttype, org_name: str, file_name: str):
    # Make a sqlalchemy session - Name of database is read from settings
    session = sessionmaker(bind=get_engine(), autoflush=False)()

    # Make the query
    data = reporttype(session, org_name)

    data_df = pd.DataFrame(data)

    # write data as csv file
    with open(file_name, "w+", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        for row in data_df.itertuples(index=False):
            writer.writerow(row)
