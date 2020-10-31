import datetime
import re
import sys
import importlib
import numpy as np
from abc import ABC
from typing import List, Union

import yaml

from june import paths
from june.demography.person import Person
from june.groups.leisure import Leisure
from june.infection.symptom_tag import SymptomTag
from june.interaction import Interaction

default_config_filename = paths.configs_path / "defaults/policy/policy.yaml"
default_regional_compliance_filename = paths.configs_path / "defaults/policy/regional_compliance.yaml"


def str_to_class(classname, base_policy_modules=("june.policy",)):
    for module_name in base_policy_modules:
        try:
            module = importlib.import_module(module_name)
            return getattr(module, classname)
        except AttributeError:
            continue
    raise ValueError("Cannot find policy in paths!")

def read_date(date: Union[str, datetime.datetime]) -> datetime.datetime:
        """
        Read date in two possible formats, either string or datetime.date, both
        are translated into datetime.datetime to be used by the simulator

        Parameters
        ----------
        date:
            date to translate into datetime.datetime

        Returns
        -------
            date in datetime format
        """
        if type(date) is str:
            return datetime.datetime.strptime(date, "%Y-%m-%d")
        elif isinstance(date, datetime.date):
            return datetime.datetime.combine(date, datetime.datetime.min.time())
        else:
            raise TypeError("date must be a string or a datetime.date object")

def regional_compliance_is_active(regional_compliance, date):

    if regional_compliance is None:
        return None

    for compliance in regional_compliance:       
        if (
                read_date(compliance["start_time"])
                <= date
                < read_date(compliance["end_time"])
        ):
            return compliance

    return None

                                                                                                            


class Policy(ABC):
    def __init__(
        self,
        start_time: Union[str, datetime.datetime] = "1900-01-01",
        end_time: Union[str, datetime.datetime] = "2100-01-01",
    ):
        """
        Template for a general policy.

        Parameters
        ----------
        start_time:
            date at which to start applying the policy
        end_time:
            date from which the policy won't apply
        """
        self.spec = self.get_spec()
        self.start_time = self.read_date(start_time)
        self.end_time = self.read_date(end_time)

    @staticmethod
    def read_date(date: Union[str, datetime.datetime]) -> datetime.datetime:
        """
        Read date in two possible formats, either string or datetime.date, both
        are translated into datetime.datetime to be used by the simulator

        Parameters
        ----------
        date:
            date to translate into datetime.datetime

        Returns
        -------
            date in datetime format
        """
        if type(date) is str:
            return datetime.datetime.strptime(date, "%Y-%m-%d")
        elif isinstance(date, datetime.date):
            return datetime.datetime.combine(date, datetime.datetime.min.time())
        else:
            raise TypeError("date must be a string or a datetime.date object")

    def get_spec(self) -> str:
        """
        Returns the speciailization of the policy.
        """
        return re.sub(r"(?<!^)(?=[A-Z])", "_", self.__class__.__name__).lower()

    def is_active(self, date: datetime.datetime) -> bool:
        """
        Returns true if the policy is active, false otherwise

        Parameters
        ----------
        date:
            date to check
        """
        return self.start_time <= date < self.end_time


class Policies:
    def __init__(self, policies=None, regional_compliance=None):
        self.policies = policies
        self.regional_compliance = regional_compliance
        # Note (Arnau): This import here is ugly, but I couldn't
        # find a way to get around a redundant import loop.
        from june.policy import (
            IndividualPolicies,
            InteractionPolicies,
            MedicalCarePolicies,
            LeisurePolicies,
        )
        self.individual_policies = IndividualPolicies.from_policies(self)
        self.interaction_policies = InteractionPolicies.from_policies(self)
        self.medical_care_policies = MedicalCarePolicies.from_policies(self)
        self.leisure_policies = LeisurePolicies.from_policies(self)

    @classmethod
    def from_file(
            cls,
            config_file=default_config_filename,
            base_policy_modules=("june.policy",),
            regional_compliance_file = default_regional_compliance_filename,
    ):
        with open(config_file) as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
        policies = []
        for policy, policy_data in config.items():
            camel_case_key = "".join(x.capitalize() or "_" for x in policy.split("_"))
            if "start_time" not in policy_data:
                for policy_i, policy_data_i in policy_data.items():
                    if (
                        "start_time" not in policy_data_i.keys()
                        or "end_time" not in policy_data_i.keys()
                    ):
                        raise ValueError("policy config file not valid.")
                    policies.append(
                        str_to_class(camel_case_key, base_policy_modules)(
                            **policy_data_i
                        )
                    )
            else:
                policies.append(
                    str_to_class(camel_case_key, base_policy_modules)(**policy_data)
                )

        with open(regional_compliance_file) as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
        regional_compliance = []
        for compliance, compliance_data in config.items():
            if "start_time" not in compliance_data:
                for compliance_i, compliance_data_i in compliance_data.items():
                    if(
                        "start_time" not in compliance_data_i.keys()
                        or "end_time" not in compliance_data_i.keys()
                    ):
                        raise ValueError("regional compliance config file not valid.")
                    regional_compliance.append(
                        compliance_data_i    
                    )
        return Policies(policies=policies, regional_compliance=regional_compliance)

    def get_policies_for_type(self, policy_type):
        return [policy for policy in self.policies if policy.policy_type == policy_type]

    def __iter__(self):
        return iter(self.policies)

    def init_policies(self, world):
        """
        This function is meant to be used for those policies that need world information to initialise,
        like policies depending on workers' behaviours during lockdown.
        """
        from june.policy import CloseCompanies, LimitLongCommute
        CloseCompanies.set_ratios(world=world)
        LimitLongCommute.get_long_commuters(people=world.people)



class PolicyCollection:

    def __init__(self, policies: List[Policy]):
        """
        A collection of like policies active on the same date
        """
        self.policies = policies
        self.policies_by_name = {self._get_policy_name(policy) : policy for policy in policies}

    def _get_policy_name(self, policy):
        return re.sub(r"(?<!^)(?=[A-Z])", "_", policy.__class__.__name__).lower()

    @classmethod
    def from_policies(cls, policies: Policies):
        return cls(policies.get_policies_for_type(policy_type=cls.policy_type))

    def get_active(self, date: datetime):
        return [policy for policy in self.policies if policy.is_active(date)]

    def apply(self, active_policies):
        raise NotImplementedError()

    def __iter__(self):
        return iter(self.policies)

    def __getitem__(self, index):
        return self.policies[index]

    def get_from_name(self, name):
        return self.policies_by_name[name]

    def __contains__(self, policy_name):
        return policy_name in self.policies_by_name

