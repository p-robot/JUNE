import numpy as np
import yaml
from typing import List
from june import paths
from june.interaction.interaction import Interaction


class ContactAveraging(Interaction):
    def __init__(self, betas, contact_matrices, selector):
        self.betas = betas
        self.contact_matrices = contact_matrices
        self.selector = selector

    def get_sum_transmission_per_subgroup(self, group: "Group")->List[float]:
        """
        Given a group, computes the sum of the transmission probabilities 
        of the infected people per subgroup

        Parameters
        ----------

        group:
            instance of group to run the interaction model on
        """
        self.transmission_per_subgroup = [
            self.get_sum_transmission_probabilities(subgroup)
            for subgroup in group.subgroups
        ]

    def get_sum_transmission_probabilities(self, subgroup: "Subgroup")->float:
        """
        Compute the sum of transmission probabilities for a subgroup

        Parameters
        ----------
        subgroup:
            an instance of subgroup 
        """
        return sum(
            [
                person.health_information.infection.transmission.probability
                for person in subgroup.infected
            ]
        )

    def subgroup_to_subgroup_transmission(
        self, susceptibles_subgroup, infecters_subgroup, group_spec
    )->float:
        """
        Computes the transmission power from one subgroup to another, given by their
        number of contacts that a susceptible person might do with an infected person in
        the other subgroup, and the total transmission probability of the other subgroup.

        Parameters
        ----------
        susceptibles_subgroup:
            subgroup containing susceptible people
        infecters_subgroup:
            subgroup containing infected people
        group_spec:
            specifier of the group
        Returns
        -------
            number of contacts a susceptible person has with infected people,
            times the average transmission probability of the subgroup with infected people
        """
        subgroup_size = len(infecters_subgroup)
        if susceptibles_subgroup == infecters_subgroup:
            subgroup_size -= 1
        susceptibles_idx = susceptibles_subgroup.subgroup_type
        infecters_idx = infecters_subgroup.subgroup_type
        n_contacts = self.contact_matrices.get(group_spec)[susceptibles_idx][
            infecters_idx
        ]
        return (
            n_contacts / subgroup_size * self.transmission_per_subgroup[infecters_idx]
        )

    def compute_effective_transmission(self, susceptibles_subgroup: "Subgroup", group: "Group", delta_time: float)->List[float]:
        '''
        Compute the effective transmission probability of infected people, by summing the 
        transmissions from different subgroups.

        Parameters
        ----------
        susceptibles_subgroup:
            subgroup containing the susceptible people
        group:
            group in which the interaction takes place
        delta_time:
            duration of the interaction

        Returns
        -------
            list of transmission probabilities for each susceptible person
        '''
        transmission_exponent = 0
        susceptibilities = np.array(
            [person.susceptibility for person in susceptibles_subgroup]
        )
        for subgroup in group.subgroups:
            if len(subgroup.infected) > 0:
                transmission_exponent += self.subgroup_to_subgroup_transmission(
                    susceptibles_subgroup, subgroup, group.spec
                )
        return 1.0 - np.exp(
            -delta_time
            * susceptibilities
            * self.betas.get(group.spec)
            * transmission_exponent
        )

    def single_time_step_for_subgroup(
            self, susceptibles_subgroup: "Subgroup", group: "Group", time: float, delta_time: float
    ):
        '''
        Run the interaction for a time step over a subgroup
        
        Parameters
        ----------
        susceptibles_subgroup: 
            subgroup with susceptible people
        group:
            group containing all subgroups
        time:
            time at which the interaction happens (in units of days)
        delta_time:
            duration of the interaction (in units of days)
        '''
        transmission_probability = self.compute_effective_transmission(
            susceptibles_subgroup, group, delta_time
        )
        should_be_infected = np.random.random(len(susceptibles_subgroup))
        for i, (recipient, luck) in enumerate(
            zip(susceptibles_subgroup, should_be_infected)
        ):
            if luck < transmission_probability[i]:
                self.selector.infect_person_at_time(person=recipient, time=time)
                recipient.health_information.update_infection_data(
                    time=time, group_type=group.spec, infecter=None, logger=None
                )

    def single_time_step_for_group(self, group: "Group", time: float, delta_time: float, logger: "Logger"):
        '''
        Run the interaction for a time step over the group.

        Parameters
        ----------
        group:
            group over which we run the interaction
        time:
            time at which the interaction happens (in units of days)
        delta_time:
            duration of the interaction (in units of days)
        logger:
            logger to save R related information 
 
        '''
        self.get_sum_transmission_per_subgroup(group)
        for susceptibles_subgroup in group.subgroups:
            if len(susceptibles_subgroup.susceptible) > 0:
                self.single_time_step_for_subgroup(
                    susceptibles_subgroup, group, time, delta_time
                )
