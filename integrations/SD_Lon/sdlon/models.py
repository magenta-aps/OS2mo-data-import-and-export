from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class JobFunction(str, Enum):
    job_position_identifier = "JobPositionIdentifier"
    employment_name = "EmploymentName"


class SDBasePerson(BaseModel):
    cpr: str
    given_name: Optional[str]
    surname: Optional[str]


class MOBasePerson(BaseModel):
    cpr: str
    givenname: str
    surname: str
    name: str
    uuid: UUID
