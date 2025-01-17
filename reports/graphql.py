from typing import Iterator

import httpx
from fastramqpi.raclients.graph.client import GraphQLClient
from gql import gql
from more_itertools import one
from pydantic import AnyHttpUrl
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_delay
from tenacity import wait_fixed

from tools.log import LogLevel
from tools.log import get_logger
from tools.log import setup_logging

setup_logging(LogLevel.DEBUG)
logger = get_logger()


def get_mo_client(
    auth_server: AnyHttpUrl,
    client_id: str,
    client_secret: str,
    mo_base_url: str,
    gql_version: int,
    timeout: int = 600,
) -> GraphQLClient:
    """
    Get the GraphQL client for calling MO.

    Args:
        auth_server: the Keycloak server
        client_id: Keycloak client ID
        client_secret: Keycloak client secret
        mo_base_url: MOs base URL
        gql_version: GraphQL version
        timeout: timeout for the client

    Returns:
        A GraphQL client
    """

    return GraphQLClient(
        url=f"{mo_base_url}/graphql/v{str(gql_version)}",
        client_id=client_id,
        client_secret=client_secret,
        auth_server=auth_server,  # type: ignore
        auth_realm="mo",
        execute_timeout=timeout,
        httpx_client_kwargs={"timeout": timeout},
        sync=True,
    )


@retry(
    wait=wait_fixed(5),
    reraise=True,
    stop=stop_after_delay(10 * 60),
    retry=retry_if_exception_type(httpx.HTTPError),
)
def query_graphql(
    graphql_client: GraphQLClient, query: str, page_size: int | None, cursor: str | None
) -> dict:
    return graphql_client.execute(
        gql(query), variable_values={"limit": page_size, "cursor": cursor}
    )


def paginated_query(
    graphql_client: GraphQLClient, query: str, page_size: int = 1000
) -> Iterator:
    cursor = None
    i = 0
    while True:
        res = query_graphql(graphql_client, query, page_size, cursor)
        query_object = one(res.keys())
        for e in res[query_object]["objects"]:
            yield e
        i += 1
        cursor = res[query_object]["page_info"]["next_cursor"]
        if cursor is None:
            break
        logger.debug(
            f"Paginated query. Now reading {i * page_size - page_size + 1}-{i * page_size}"
        )
