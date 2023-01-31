# -- coding: utf-8 --
#
# Copyright (c) 2018, Magenta ApS
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import hashlib
import json
import logging
from typing import Dict
from uuid import UUID

import requests
from os2sync_export import config
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_delay
from tenacity import wait_fixed

retry_max_time = 60
settings = config.get_os2sync_settings()
logger = logging.getLogger(__name__)
hash_cache: Dict = {}


def get_os2sync_session():

    session = requests.Session()

    if settings.os2sync_api_url == "stub":
        from os2sync_export import stub

        session = stub.Session()

    session.verify = settings.os2sync_ca_verify_os2sync
    session.headers["User-Agent"] = "os2mo-data-import-and-export"
    session.headers["CVR"] = settings.municipality
    return session


session = get_os2sync_session()


def already_xferred(url, params, method):
    if settings.os2sync_api_url == "stub":
        params_hash = params
    else:
        params_hash = hashlib.sha224(
            (json.dumps(params, sort_keys=True) + method).encode("utf-8")
        ).hexdigest()
    if hash_cache.get(url) == params_hash:
        return True
    else:
        hash_cache[url] = params_hash
    return False


def os2sync_url(url):
    """format url like {BASE}/user"""
    url = url.format(BASE=settings.os2sync_api_url)
    return url


def os2sync_get(url, **params):
    url = os2sync_url(url)
    r = session.get(url, params=params)
    r.raise_for_status()
    return r


def os2sync_delete(url, **params):
    url = os2sync_url(url)
    try:
        r = session.delete(url, **params)
        r.raise_for_status()
        return r
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning("delete %r %r :404", url, params)
            return r


def os2sync_post(url, **params):
    url = os2sync_url(url)
    r = session.post(url, **params)
    r.raise_for_status()
    return r


def user_uuids():
    return os2sync_get("{BASE}/user").json()


def delete_user(uuid):
    if not already_xferred("/user/" + uuid, {}, "delete"):
        logger.debug("delete user %s", uuid)
        os2sync_delete("{BASE}/user/" + uuid)
    else:
        logger.debug("delete user %s - cached", uuid)


def upsert_user(user):
    if not already_xferred("/user/" + user["Uuid"], user, "upsert"):
        logger.debug("upsert user %s", user["Uuid"])
        os2sync_post("{BASE}/user", json=user)
    else:
        logger.debug("upsert user %s - cached", user["Uuid"])


def orgunit_uuids():
    return os2sync_get("{BASE}/orgunit").json()


def delete_orgunit(uuid):
    if not already_xferred("/orgUnit/" + uuid, {}, "delete"):
        logger.debug("delete orgunit %s", uuid)
        os2sync_delete("{BASE}/orgUnit/" + uuid)
    else:
        logger.debug("delete orgunit %s - cached", uuid)


def upsert_orgunit(org_unit):
    if not already_xferred("/orgUnit/" + org_unit["Uuid"], org_unit, "upsert"):
        logger.debug("upsert orgunit %s", org_unit["Uuid"])
        os2sync_post("{BASE}/orgUnit/", json=org_unit)
    else:
        logger.debug("upsert orgunit %s - cached", org_unit["Uuid"])


def trigger_hierarchy(client: requests.Session, os2sync_api_url: str) -> UUID:
    """ "Triggers a job in the os2sync container that gathers the entire hierarchy from FK-ORG

    Returns: UUID

    """
    r = client.get(f"{os2sync_api_url}/hierarchy")
    r.raise_for_status()
    return UUID(r.text)


@retry(
    wait=wait_fixed(5),
    reraise=True,
    stop=stop_after_delay(5 * 60),
    retry=retry_if_exception_type(requests.HTTPError),
)
def get_hierarchy(client: requests.Session, os2sync_api_url: str, request_uuid: UUID):
    """Fetches the hierarchy from os2sync. Retries for 5 minutes until it is ready"""
    r = client.get(f"{os2sync_api_url}/hierarchy/{str(request_uuid)}")
    r.raise_for_status()
    hierarchy = r.json()["result"]
    if hierarchy is None:
        raise ConnectionError("Check connection to FK-ORG")
    return hierarchy
