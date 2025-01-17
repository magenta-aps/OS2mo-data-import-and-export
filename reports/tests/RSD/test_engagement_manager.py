import pytest
from freezegun import freeze_time

from reports.RSD.Engagement_manager import extract_ancestors
from reports.RSD.Engagement_manager import find_managers_of_type
from reports.RSD.Engagement_manager import get_age
from reports.RSD.Engagement_manager import has_responsibility


@pytest.mark.parametrize(
    "freeze_date,expected_age",
    (("1970-01-01", 10), ("1991-07-17", 31), ("2045-12-31", 85)),
)
def test_get_age_at_dates(freeze_date, expected_age):
    cpr = "0101601296"
    with freeze_time(freeze_date):
        assert get_age(cpr) == expected_age


@pytest.mark.parametrize(
    "ancestors,expected_list",
    (
        # Test top unit that has no ancestors
        ([], ["", "", "", "", "", "", ""]),
        # Test a unit with one ancestor
        ([{"name": "Niveau 1"}], ["Niveau 1", "", "", "", "", "", ""]),
        # Test 8 that with levels of ancestors we return the top 7.
        (
            [
                {"name": "Niveau 8"},
                {"name": "Niveau 7"},
                {"name": "Niveau 6"},
                {"name": "Niveau 5"},
                {"name": "Niveau 4"},
                {"name": "Niveau 3"},
                {"name": "Niveau 2"},
                {"name": "Niveau 1"},
            ],
            [
                "Niveau 1",
                "Niveau 2",
                "Niveau 3",
                "Niveau 4",
                "Niveau 5",
                "Niveau 6",
                "Niveau 7",
            ],
        ),
    ),
)
def test_extract_ancestors(ancestors, expected_list):
    assert extract_ancestors(ancestors) == expected_list


def test_find_managers_of_type():
    manager = {"name": "LEDER", "manager_type": {"name": "Leder"}}
    co_manager_1 = {"name": "MEDLEDER1", "manager_type": {"name": "Medleder"}}
    co_manager_2 = {"name": "MEDLEDER2", "manager_type": {"name": "Medleder"}}
    admin = {"name": "Administrator", "manager_type": {"name": "Administrator"}}
    irrelevant_manager = {"name": "IRRELEVANT", "manager_type": {"name": "irrelevant"}}

    manager_list = [manager, co_manager_1, co_manager_2, admin, irrelevant_manager]
    assert find_managers_of_type(manager_list, "Leder") == [manager]
    assert find_managers_of_type(manager_list, "Medleder") == [
        co_manager_1,
        co_manager_2,
    ]
    assert find_managers_of_type(manager_list, "Administrator") == [admin]


def test_has_responsibility():
    assert not has_responsibility(
        {"responsibilities": []}, "Ansvarlig for Sommerfesten"
    )
    assert has_responsibility(
        {"responsibilities": [{"name": "Ansvarlig for Sommerfesten"}]},
        "Ansvarlig for Sommerfesten",
    )
