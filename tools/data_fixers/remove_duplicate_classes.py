import json
import urllib.parse
from collections import Counter
from operator import itemgetter
from typing import List, Tuple
from uuid import UUID
import click
import jmespath
import requests
from more_itertools import only
from tqdm import tqdm

from exporters.utils.load_settings import load_settings

jms_title_list = jmespath.compile(
    "[*].registreringer[0].attributter.klasseegenskaber[0].titel"
)
jms_bvn_one = jmespath.compile(
    "registreringer[0].attributter.klasseegenskaber[0].brugervendtnoegle"
)


def check_relations(session, base: str, uuid: UUID) -> List[dict]:
    """Find all objects related to the class with the given uuid.

    Returns a list of objects, or an empty list if no objects related to the given uuid are found.
    """
    r = session.get(
        base
        + f"/organisation/organisationfunktion?vilkaarligrel={str(uuid)}&list=true&virkningfra=-infinity"
    )
    r.raise_for_status()
    res = r.json()["results"]
    return only(res, default=[])


def read_duplicate_class(session, base: str, bvn: str) -> List[Tuple[UUID, str]]:
    """Read details of classes with the given bvn.

    Returns a list of tuples with uuids and titles of the found classes.
    """
    bvn = urllib.parse.quote(bvn)
    r = session.get(base + f"/klassifikation/klasse?brugervendtnoegle={bvn}&list=true")
    r.raise_for_status()
    res = r.json()["results"][0]
    uuids = jmespath.search("[*].id", res)
    uuids = map(UUID, uuids)
    titles = jms_title_list.search(res)
    return list(zip(uuids, titles))


def delete_class(session, base: str, uuid: UUID) -> None:
    """Delete the class with the given uuid."""
    r = session.delete(base + f"/klassifikation/klasse/{str(uuid)}")
    r.raise_for_status()


def switch_class(session, base: str, payload: str, new_uuid: UUID, uuid_set: set) -> None:
    """Switch an objects related class.

    Given an object payload and an uuid this function wil switch the class that an object is related to.
    Only switches class if it is in the set uuid_set.
    """
    old_uuid = UUID(payload["id"])
    payload = payload["registreringer"][0]
    #Drop data we don't need to post
    payload = {
        item: payload.get(item) for item in ("attributter", "relationer", "tilstande")
    }
    org_f_type = payload["relationer"]["organisatoriskfunktionstype"]
    #Update the uuid of all classes if the class is in uuid_set
    #This is to ensure we only update the classes that would otherwise be deleted
    [x.update({"uuid": str(new_uuid)}) for x in org_f_type if UUID(x['uuid']) in uuid_set]
    r = session.put(
        base + f"/organisation/organisationfunktion/{str(old_uuid)}", json=payload
    )
    r.raise_for_status()


def find_duplicates_classes(session, mox_base: str) -> List[str]:
    """Find classes that are duplicates and return them.

    Returns a list of class bvns that has duplicates. They are returned in lowercase.
    """
    r = session.get(mox_base + "/klassifikation/klasse?list=true")
    all_classes = r.json()["results"][0]
    all_ids = map(itemgetter("id"), all_classes)
    all_classes = list(map(lambda c: jms_bvn_one.search(c).lower(), all_classes))
    class_map = dict(zip(all_classes, all_ids))
    duplicate_list = [i for i, cnt in Counter(all_classes).items() if cnt > 1]
    return duplicate_list


@click.command()
@click.option(
    "--delete",
    type=click.BOOL,
    default=False,
    is_flag=True,
    required=False,
    help="Remove any class that has duplicates",
)
def cli(delete):
    """Tool to help remove classes from MO that are duplicates.

    This tool is written to help clean up engagement_types that had the same name, but with different casing.
    If no argument is given it will print the amount of duplicated classses.
    If the `--delete` flag is supplied you will be prompted to choose a class to keep for each duplicate.
    In case there are no differences to
    Objects related to the other class will be transferred to the selected class and the other class deleted.
    """

    settings = load_settings()
    mox_base = settings.get("mox.base", "http://localhost:8080/")
    session = requests.Session()

    duplicate_list = find_duplicates_classes(session=session, mox_base=mox_base)

    if not delete:
        click.echo(f"There are {len(duplicate_list)} duplicate class(es).")
        return

    for dup in tqdm(duplicate_list, desc="Deleting duplicate classes"):

        dup_class = read_duplicate_class(session, mox_base, dup)
        title_set = set(map(itemgetter(1), dup_class))
        uuid_set = set(map(itemgetter(0), dup_class))
        # Check if all found titles are exactly the same. Only prompt for a choice if they are not.
        keep = 1
        if len(title_set) != 1:
            click.echo("These are the choices:")
            # Generate a prompt to display
            msg = "\n".join(f"  {i}: {x[1]}" for i, x in enumerate(dup_class, start=1))
            click.echo(msg)
            keep = click.prompt("Choose the one to keep", type=int, default=1)
        kept_uuid, _ = dup_class[keep - 1]
        for i, obj in enumerate(dup_class, start=1):
            if i == keep:
                continue
            uuid, _ = obj
            rel = check_relations(session, mox_base, uuid)
            for payload in tqdm(rel, desc="Changing class for objects"):
                switch_class(session, mox_base, payload, kept_uuid, uuid_set)
            delete_class(session, mox_base, uuid)


if __name__ == "__main__":
    cli()
