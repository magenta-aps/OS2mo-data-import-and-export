import datetime
import logging
from dataclasses import dataclass
from typing import Iterator

import click
import dateutil.parser
import httpx
import sentry_sdk
from fastapi.encoders import jsonable_encoder
from gql import gql
from ra_utils.job_settings import JobSettings
from ra_utils.load_settings import load_setting
from ra_utils.tqdm_wrapper import tqdm
from raclients.graph.client import GraphQLClient
from raclients.graph.client import SyncClientSession
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_delay
from tenacity import wait_fixed

from integrations.ad_integration.ad_common import AD
from integrations.ad_integration.ad_reader import ADParameterReader


logger = logging.getLogger(__name__)


class Unset:
    def __repr__(self) -> str:
        return "Unset()"

    def __eq__(self, other) -> bool:
        if isinstance(other, Unset):
            return True
        return super().__eq__(other)


class AdFixEndDateSettings(JobSettings):
    lookahead_days = 0

    class Config:
        settings_json_prefix = "integrations.ad.write"


class MOEngagementDateSource:
    _ad_null_date = datetime.date(9999, 12, 31)

    def __init__(
        self,
        graphql_session: SyncClientSession,
        lookahead_days: int,
    ):
        self._graphql_session: SyncClientSession = graphql_session
        self._lookahead_days = lookahead_days

    def to_enddate(self, date_str: str | None) -> datetime.date:
        """
        Takes a string and converts it to a date, taking into account that when an
        engagement does not have an end date, MO handles it as None, while AD handles it
        as "9999-12-31".
        """
        if not date_str:
            return self._ad_null_date
        end_date = dateutil.parser.parse(date_str).date()
        if end_date.year == self._ad_null_date.year:
            return self._ad_null_date
        return end_date

    @retry(
        wait=wait_fixed(5),
        reraise=True,
        stop=stop_after_delay(10 * 60),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def get_employee_end_date(self, uuid: str) -> datetime.date:
        query = gql(
            """
            query Get_mo_engagements($to_date: DateTime, $employees: [UUID!]) {
                engagements(from_date: null, to_date: $to_date, employees: $employees) {
                    objects {
                        validity {
                            to
                        }
                    }
                }
            }
            """
        )

        now = datetime.datetime.now()
        lookahead = datetime.timedelta(days=self._lookahead_days)
        to_date = now + lookahead
        to_date = to_date.astimezone()

        result = self._graphql_session.execute(
            query,
            variable_values=jsonable_encoder({"to_date": to_date, "employees": uuid}),
        )

        if not result["engagements"]:
            raise KeyError("User not found in mo")

        end_dates = [
            self.to_enddate(obj["validity"]["to"])
            for engagement in result["engagements"]
            for obj in engagement["objects"]
        ]

        return max(end_dates)


@dataclass
class ADUserEndDate:
    mo_uuid: str
    end_date: str | None


class ADEndDateSource:
    def __init__(
        self, uuid_field: str, enddate_field: str, settings: dict | None = None
    ):
        self._uuid_field = uuid_field
        self._enddate_field = enddate_field
        self._reader = ADParameterReader(all_settings=settings)

    def get_all_matching_mo(self) -> Iterator[ADUserEndDate]:
        for ad_user in self._reader.read_it_all():
            if self._uuid_field not in ad_user:
                click.echo(
                    f"User with {ad_user['ObjectGuid']=} does not have an "
                    f"{self._uuid_field} field, and will be skipped"
                )
                continue

            yield ADUserEndDate(
                ad_user[self._uuid_field],
                ad_user.get(self._enddate_field, None),
            )


class CompareEndDate:
    def __init__(
        self,
        enddate_field: str,
        mo_engagement_date_source: MOEngagementDateSource,
        ad_end_date_source: ADEndDateSource,
    ):
        self.enddate_field = enddate_field
        self._mo_engagement_date_source = mo_engagement_date_source
        self._ad_end_date_source = ad_end_date_source

    def get_end_dates_to_fix(self, show_date_diffs: bool) -> dict:
        # Compare AD users to MO users
        print("Find users from AD")
        ad_users: list[ADUserEndDate] = list(
            self._ad_end_date_source.get_all_matching_mo()
        )
        end_dates_to_fix = {}
        print("Compare to MO engagement data per user")
        for ad_user in tqdm(ad_users, unit="user"):
            uuid = ad_user.mo_uuid

            try:
                mo_end_date = self._mo_engagement_date_source.get_employee_end_date(
                    uuid
                )
            except KeyError:
                continue
            else:
                mo_end_date = mo_end_date.strftime("%Y-%m-%d")

            if ad_user.end_date is None:
                logger.info(
                    "User %s does not have the field %r", uuid, self.enddate_field
                )
                # if the user does not have an end date, give it one
                end_dates_to_fix[uuid] = mo_end_date
                continue

            if ad_user.end_date == mo_end_date:
                continue

            end_dates_to_fix[uuid] = mo_end_date

        if show_date_diffs:
            for ad_user in ad_users:
                uuid = ad_user.mo_uuid
                if uuid in end_dates_to_fix:
                    if ad_user.end_date:
                        ad_end = ad_user.end_date
                    else:
                        ad_end = "None"
                    logger.info(
                        f"User {uuid} has AD end date: {ad_end} and MO end date: "
                        f"{end_dates_to_fix[uuid]}"
                    )

        return end_dates_to_fix


class UpdateEndDate(AD):
    def __init__(self, settings=None):
        super().__init__(all_settings=settings)

    def get_update_cmd(
        self,
        uuid_field: str,
        uuid: str,
        end_date_field: str,
        end_date: str,
    ):
        cmd_f = """
        Get-ADUser %(complete)s -Filter '%(uuid_field)s -eq "%(uuid)s"' |
        Set-ADUser %(credentials)s -Replace @{%(enddate_field)s="%(end_date)s"} |
        ConvertTo-Json
        """
        cmd = cmd_f % dict(
            uuid=uuid,
            end_date=end_date,
            enddate_field=end_date_field,
            uuid_field=uuid_field,
            complete=self._ps_boiler_plate()["complete"],
            credentials=self._ps_boiler_plate()["credentials"],
        )
        return cmd

    def run(self, cmd) -> dict:
        return self._run_ps_script("%s\n%s" % (self._build_user_credential(), cmd))

    def update_all(
        self,
        end_dates_to_fix,
        uuid_field: str,
        end_date_field: str,
        print_commands: bool = False,
        dry_run: bool = False,
    ) -> list:
        retval = []
        for uuid, end_date in tqdm(
            end_dates_to_fix.items(), unit="user", desc="Changing enddate in AD"
        ):
            cmd = self.get_update_cmd(uuid_field, uuid, end_date_field, end_date)
            if print_commands:
                logger.info("Command to run: ")
                logger.info(cmd)

            if not dry_run:
                result = self.run(cmd)
                if result:
                    logger.info("Result: %r", result)
                retval.append((cmd, result))
            else:
                retval.append((cmd, "<dry run>"))  # type: ignore

        logger.info("%d users end dates corrected", len(end_dates_to_fix))
        logger.info("All end dates are fixed")

        return retval


@click.command()
@click.option(
    "--enddate-field",
    default=load_setting("integrations.ad_writer.fixup_enddate_field"),
)
@click.option("--uuid-field", default=load_setting("integrations.ad.write.uuid_field"))
@click.option("--dry-run", is_flag=True)
@click.option("--show-date-diffs", is_flag=True)
@click.option("--print-commands", is_flag=True)
@click.option("--mora-base", envvar="MORA_BASE", default="http://mo")
@click.option("--client-id", envvar="CLIENT_ID", default="dipex")
@click.option("--client-secret", envvar="CLIENT_SECRET")
@click.option("--auth-realm", envvar="AUTH_REALM", default="mo")
@click.option("--auth-server", envvar="AUTH_SERVER", default="http://keycloak")
def cli(
    enddate_field,
    uuid_field,
    dry_run,
    show_date_diffs,
    print_commands,
    mora_base: str,
    client_id: str,
    client_secret: str,
    auth_realm: str,
    auth_server: str,
):
    """Fix enddates of terminated users.
    AD-writer does not support writing enddate of a terminated employee,
    this script finds and corrects the enddate in AD of terminated engagements.
    """
    pydantic_settings = AdFixEndDateSettings()
    pydantic_settings.start_logging_based_on_settings()

    if pydantic_settings.sentry_dsn:
        sentry_sdk.init(dsn=pydantic_settings.sentry_dsn)

    logger.info(
        f"Command line args:"
        f" end-date-field = {enddate_field},"
        f" uuid-field = {uuid_field},"
        f" dry-run = {dry_run},"
        f" show-date-diffs = {show_date_diffs},"
        f" print-commands = {print_commands},"
        f" mora-base = {mora_base},"
        f" client-id = {client_id},"
        f" client-secret = not logged,"
        f" auth-realm = {auth_realm},"
        f" auth-server = {auth_server}",
    )

    graphql_client = GraphQLClient(
        url=f"{mora_base}/graphql/v3",
        client_id=client_id,
        client_secret=client_secret,
        auth_realm=auth_realm,
        auth_server=auth_server,
        sync=True,
        httpx_client_kwargs={"timeout": None},
    )

    with graphql_client as session:
        mo_engagement_date_source = MOEngagementDateSource(
            session,
            pydantic_settings.lookahead_days,
        )
        ad_end_date_source = ADEndDateSource(
            uuid_field,
            enddate_field,
        )
        c = CompareEndDate(
            enddate_field,
            mo_engagement_date_source,
            ad_end_date_source,
        )
        end_dates_to_fix = c.get_end_dates_to_fix(show_date_diffs)
        u = UpdateEndDate()
        u.update_all(
            end_dates_to_fix,
            uuid_field,
            enddate_field,
        )


if __name__ == "__main__":
    cli()
