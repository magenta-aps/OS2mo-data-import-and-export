"""Generates a report for RSD containing information on org_units, managers and engagements"""


import click
from fastramqpi.ra_utils.job_settings import JobSettings
from reports.graphql import get_mo_client
from reports.graphql import paginated_query
from tools.log import LogLevel
from tools.log import get_logger
from tools.log import setup_logging

setup_logging(LogLevel.DEBUG)
logger = get_logger()

HEADERS = (
    "Niveau 1",
    "Niveau 2",
    "Niveau 3",
    "Niveau 4",
    "Niveau 5",
    "Niveau 6",
    "Niveau 7",
    "Enhedsnavn",
    "Leder",
    "Medleder 1",
    "Medleder 2",
    "Administrator",
    "Administrativ ansvarlig",
    "Personaleledelse",
    "Uddannelsesansvarlig",
    "BVN (k)",
    "Tjenestenummer",
    "Medarbejder",
    "E-mail",
    "Stillingskode nuværende",
)
HEADERS_2 = [
    "Niveau 1",
    "Niveau 2",
    "Niveau 3",
    "Niveau 4",
    "Niveau 5",
    "Niveau 6",
    "Niveau 7",
    "Enhedsnavn",
    "Enhedstype",
    "Medarbejder",
    "Stilling",
    "Lederbetegnelse",
    "Lederansvar",
    "Tjenestenummer",
    "CPR-nummer",
    "E-mail",
    "Alder",
    "Leder",
    "BVN (k)",
]


QUERY = """
query EngagementManagers($limit: int, $cursor: Cursor = null) {
  org_units(limit: $limit, cursor: $cursor, filter: { ancestor: { uuids: "00923955-db6e-49fc-a191-ec36ff151ec7" } }) {
    objects {
      current {
        uuid
        name
        managers(inherit: false) {
          user_key
          responsibilities {
            name
          }
          person {
            name
            uuid
          }
          manager_type {
            name
          }
        }
        ancestors {
          name
        }
        unit_type {
          name
        }
        engagements {
          person {
            name
            cpr_number
            uuid
            addresses(filter: { address_type: { scope: "EMAIL" } }) {
              name
            }
          }
          user_key
          job_function {
            name
            user_key
          }
          extension_1
        }
      }
    }
    page_info {
      next_cursor
    }
  }
}
"""


@click.command()
@click.option("--mora_base", envvar="MORA_BASE", default="http://mo-service:5000")
@click.option("--client-id", envvar="CLIENT_ID", default="dipex")
@click.option("--client-secret", envvar="CLIENT_SECRET")
@click.option("--auth-realm", envvar="AUTH_REALM", default="mo")
@click.option(
    "--auth-server", envvar="AUTH_SERVER", default="http://keycloak-service:8080/auth"
)
def main(*args, **kwargs):
    settings = JobSettings(**kwargs)
    client = get_mo_client(
        auth_server=settings.auth_server,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        mo_base_url=settings.mora_base,
        gql_version=22,
    )
    res = paginated_query(graphql_client=client, query=QUERY, page_size=10)
    print(res)

if __name__ == "__main__":
    logger.info("starting engagement_report")
    main()
    logger.info("done")
