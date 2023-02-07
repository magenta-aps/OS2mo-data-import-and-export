import os
from ra_utils.job_settings import JobSettings

from reports.query_actualstate import list_employees, list_MED_members, run_report
from exporters import common_queries as cq
from os2mo_helpers.mora_helpers import MoraHelper
from exporters.ballerup import export_udvalg
from pathlib import Path


MORA_BASE = os.environ.get("MORA_BASE", "http://localhost:5000")


if __name__ == "__main__":
    # Læs fra settings
    settings = JobSettings()
    settings.start_logging_based_on_settings()

    mh = MoraHelper(hostname=MORA_BASE, export_ansi=False, use_cache=True)
    org = mh.read_organisation()
    roots = mh.read_top_units(org)

    for root in roots:
        if root["name"] == "Holstebro Kommune":
            holstebro = root["uuid"]

        if root["name"] == "MED":
            test_med = root["uuid"]

    nodes = mh.read_ou_tree(holstebro)

    path = Path("/opt/docker/os2mo/queries/")

    filename = "/mora/folder/query_export/Test_Alle_lederfunktioner_os2mo.csv"
    cq.export_managers(mh, nodes, filename, empty_manager_fields=True)

    filename = "/mora/folder/query_export/Test_AlleBK-stilling-email_os2mo.csv"
    cq.export_all_employees(mh, nodes, filename)

    filename = (
        "/mora/folder/query_export/Test_Holstebro_org_incl-medarbejdere_os2mo.csv"
    )
    cq.export_orgs(mh, nodes, filename)

    filename = "/mora/folder/query_export/Test_Adm-org-incl-start-og-stopdata-og-enhedstyper-os2mo.csv"
    cq.export_adm_org(mh, nodes, filename)

    print("IT REACHES THIS POINT 1")
    filename = "Test_teams_teamteams-tilknyttede-os2mo.csv"
    cq.export_all_teams(mh, nodes, filename)
    print("IT REACHES THIS POINT 2")

    nodes = mh.read_ou_tree(test_med)
    filename = "/mora/folder/query_export/AMR-udvalgsmedlemer_i_hieraki.csv"
    fieldnames = ["Hoved-MED", "Center-MED", "Lokal-MED", "AMR-Gruppe"]
    org_types = ["AMR"]
    export_udvalg(mh, nodes, filename, fieldnames, org_types)

    filename = "/mora/folder/query_export/MED-udvalgsmedlemer_i_hieraki.csv"
    fieldnames = ["Hoved-MED", "Center-MED", "Lokal-MED", "AMR-Gruppe"]
    org_types = ["H-MED", "C-MED", "L-MED"]
    export_udvalg(mh, nodes, filename, fieldnames, org_types)

    run_report(
        list_MED_members,
        "MED",
        {"løn": "Holstebro Kommune", "MED": "MED-organisationen"},
        "/mora/folder/query_export/MED_medlemmer.xlsx",
    )
    run_report(
        list_employees,
        "Ansatte",
        "Holstebro Kommune",
        "/mora/folder/query_export/Ansatte.xlsx",
    )
