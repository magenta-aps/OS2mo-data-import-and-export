import datetime
from unittest.mock import Mock
from unittest.mock import patch

import pytest as pytest
from hypothesis import given
from hypothesis import HealthCheck
from hypothesis import settings
from hypothesis import strategies as st

from ..ad_fix_enddate import CompareEndDate
from ..ad_fix_enddate import MOEngagementDateSource
from ..ad_fix_enddate import UpdateEndDate
from .mocks import AD_UUID_FIELD
from .mocks import MO_UUID
from .mocks import MockADParameterReader


ENDDATE_FIELD = "enddate_field"
TEST_SEARCH_BASE = "search_base"
TEST_SETTINGS = {
    "primary": {
        "search_base": TEST_SEARCH_BASE,
        "system_user": "username",
        "password": "password",
    },
}


class _TestableCompareEndDateNoMatchingADUser(CompareEndDate):
    def __init__(self, mo_engagement_date_source: MOEngagementDateSource):
        super().__init__(
            ENDDATE_FIELD,
            AD_UUID_FIELD,
            mo_engagement_date_source,
            settings=TEST_SETTINGS,
        )

    def get_all_ad_users(self):
        return MockADParameterReader().read_it_all()


class _TestableCompareEndDateADUserHasMOUUID(_TestableCompareEndDateNoMatchingADUser):
    def get_all_ad_users(self):
        ad_users = super().get_all_ad_users()
        for ad_user in ad_users:
            ad_user[AD_UUID_FIELD] = MO_UUID
        return ad_users


class _TestableCompareEndDateADUserUpToDate(_TestableCompareEndDateADUserHasMOUUID):
    def get_all_ad_users(self):
        ad_users = super().get_all_ad_users()
        for ad_user in ad_users:
            ad_user[ENDDATE_FIELD] = "2022-12-31"
        return ad_users


class _TestableUpdateEndDate(UpdateEndDate):
    def __init__(self):
        super().__init__(ENDDATE_FIELD, AD_UUID_FIELD, settings=TEST_SETTINGS)


def _get_mock_graphql_session(return_value):
    graphql_session = Mock()
    graphql_session.execute = Mock()
    graphql_session.execute.return_value = return_value
    return graphql_session


@pytest.fixture()
def mock_graphql_session():
    return _get_mock_graphql_session(
        {"engagements": [{"objects": [{"validity": {"to": "2022-12-31"}}]}]}
    )


@pytest.fixture()
def mock_graphql_session_raising_keyerror():
    return _get_mock_graphql_session({})


@pytest.fixture()
def mock_mo_engagement_date_source(mock_graphql_session):
    return MOEngagementDateSource(mock_graphql_session, 0)


@pytest.fixture()
def mock_mo_engagement_date_source_raising_keyerror(
    mock_graphql_session_raising_keyerror,
):
    return MOEngagementDateSource(mock_graphql_session_raising_keyerror, 0)


@pytest.fixture()
def mock_compare_end_date(mock_mo_engagement_date_source: MOEngagementDateSource):
    with patch("integrations.ad_integration.ad_common.AD._create_session"):
        return _TestableCompareEndDateADUserHasMOUUID(mock_mo_engagement_date_source)


@given(date=st.datetimes())
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_to_enddate(date, mock_mo_engagement_date_source):
    assert mock_mo_engagement_date_source.to_enddate(str(date)) == date.date()
    assert (
        mock_mo_engagement_date_source.to_enddate(None)
        == MOEngagementDateSource._ad_null_date
    )
    assert (
        mock_mo_engagement_date_source.to_enddate("9999-12-31")
        == MOEngagementDateSource._ad_null_date
    )


@pytest.mark.parametrize(
    "eng",
    [
        {
            "engagements": [
                {"objects": [{"validity": {"to": "2021-09-02T00:00:00+02:00"}}]},
                {"objects": [{"validity": {"to": "2022-09-02T00:00:00+02:00"}}]},
                {"objects": [{"validity": {"to": "2023-09-02T00:00:00+02:00"}}]},
            ]
        },
        {
            "engagements": [
                {
                    "objects": [
                        {"validity": {"to": "2021-09-02T00:00:00+02:00"}},
                        {"validity": {"to": "2022-09-02T00:00:00+02:00"}},
                        {"validity": {"to": "2023-09-02T00:00:00+02:00"}},
                    ]
                }
            ]
        },
    ],
)
def test_get_employee_end_date(eng):
    mo_engagement_date_source = MOEngagementDateSource(
        _get_mock_graphql_session(eng), 0
    )
    known_latest_date = datetime.date(2023, 9, 2)
    found_latest_date = mo_engagement_date_source.get_employee_end_date(
        MO_UUID,
    )
    print(found_latest_date)
    assert found_latest_date == known_latest_date


@patch("integrations.ad_integration.ad_common.AD._create_session")
@given(uuid=st.uuids(), enddate=st.dates())
def test_get_update_cmd(mock_session, uuid, enddate):
    u = _TestableUpdateEndDate()
    cmd = u.get_update_cmd(uuid, enddate)
    assert (
        cmd
        == f"""
        Get-ADUser  -SearchBase "{TEST_SEARCH_BASE}"  -Credential $usercredential -Filter \'{AD_UUID_FIELD} -eq "{uuid}"\' |
        Set-ADUser  -Credential $usercredential -Replace @{{{ENDDATE_FIELD}="{enddate}"}} |
        ConvertTo-Json
        """
    )


@patch("integrations.ad_integration.ad_common.AD._create_session")
@pytest.mark.parametrize(
    "cls,expected_result",
    [
        # If no matching AD user, don't return a MO user UUID and MO end date
        (_TestableCompareEndDateNoMatchingADUser, {}),
        # If matching AD user exists *and* its AD end date is already up to date, don't
        # return a MO user UUID and MO end date.
        (_TestableCompareEndDateADUserUpToDate, {}),
        # If matching AD user exists *but* its AD end date is *not* up to date, return
        # the MO user UUID and MO end date.
        (_TestableCompareEndDateADUserHasMOUUID, {MO_UUID: "2022-12-31"}),
    ],
)
def test_get_end_dates_to_fix(
    mock_create_session, mock_mo_engagement_date_source, cls, expected_result
):
    instance = cls(mock_mo_engagement_date_source)
    actual_result = instance.get_end_dates_to_fix(MO_UUID)
    assert actual_result == expected_result


@patch("integrations.ad_integration.ad_common.AD._create_session")
def test_get_end_dates_to_fix_handles_keyerror(
    mock_create_session,
    mock_mo_engagement_date_source_raising_keyerror,
):
    instance = _TestableCompareEndDateADUserHasMOUUID(
        mock_mo_engagement_date_source_raising_keyerror
    )
    assert instance.get_end_dates_to_fix(MO_UUID) == {}
