import psutil
import os
import pickle
import time
import datetime as dt
import yaml
from itertools import combinations
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import pandas as pd
#import seaborn as sns

#from june_runs import Runner

#default_run_config_path = (
#    "/home/aidan/covid/june_runs/example_run/runs/run_000/parameters.json"#"/home/aidan/covid/june_runs/configuration/run_sets/quick_examples/local_example.yaml"
#)

from june.hdf5_savers import generate_world_from_hdf5
from june.groups.leisure import generate_leisure_for_config
from june.groups.group import Group, Subgroup
from june.interaction import Interaction
from june.interaction.interaction import _get_contacts_in_school
from june.infection import HealthIndexGenerator
from june.infection_seed import InfectionSeed, Observed2Cases
from june.infection import InfectionSelector, HealthIndexGenerator
from june.groups.travel import Travel
from june.policy import Policies
from june.records import Record
from june.demography import Person

from june import paths
from june.simulator import Simulator

default_simulation_config_path = (
    paths.configs_path / "config_example.yaml"
)
default_interaction_path = (
    paths.configs_path / "defaults/interaction/interaction.yaml"
)

default_pkl_path = Path(__file__).parent / "tracker.pkl"

default_contact_data_paths = {
    "bbc": paths.data_path / "plotting/contact_tracking/BBC.csv",
    "polymod": paths.data_path / "plotting/contact_tracking/polymod.csv",
}

class ContactTracker:

    def __init__(
        self, 
        world=None, 
        age_bins = {"5yr": np.arange(0,105,5)},
        contact_counts=None,
        contact_matrices=None,
        simulation_days=7,
        interaction_type="1d",
        pickle_path=default_pkl_path
    ):
        self.world = world
        self.age_bins = {"syoa": np.arange(0,101,1), **age_bins}
        self.simulation_days = simulation_days
        self.pickle_path = pickle_path

        self.group_types = [
            self.world.care_homes,
            self.world.cinemas, 
            self.world.city_transports,
            self.world.inter_city_transports, 
            self.world.companies,
            self.world.groceries, 
            self.world.hospitals, 
            self.world.households, 
            self.world.pubs, 
            self.world.schools, 
            self.world.universities
        ]

        if contact_matrices is None:
            self.initialise_contact_matrices(self.age_bins)
        else:
            self.contact_matrices = contact_matrices

        self.contact_types = (
            list(self.contact_matrices["syoa"].keys()) 
            + ["care_home_visits", "household_visits"]
        )

        if interaction_type in ["1d", "network"]:
            self.interaction_type = interaction_type
        else:
            raise IOError(f"No interaction type {interaction_type}. Choose from",["1d", "network"])

        if contact_counts is None:
            self.intitalise_contact_counters()
        else:
            self.contact_counts = contact_counts

        self.hash_ages() # store all ages/ index to age bins in python dict for quick lookup.
        self.load_interactions()

    @classmethod
    def from_pickle(cls, world, pickle_path=default_pkl_path):
        with open(pickle_path,'rb') as pkl:
            print("Loading from pkl...")
            tracker = pickle.load(pkl)          
            contact_matrices = tracker["contact_matrices"]
            contact_counts = tracker["contact_counts"]
            age_bins = tracker["age_bins"]
            simulation_days = tracker["simulation_days"]

        return cls(
            world, 
            age_bins=age_bins,
            contact_counts=contact_counts, 
            contact_matrices=contact_matrices,
            simulation_days=simulation_days
        )

    def initialise_contact_matrices(self, age_bins):
        self.contact_matrices = {}
        # For each type of contact matrix binning, eg BBC, polymod, SYOA...
        for bin_type, bins in self.age_bins.items():
            self.contact_matrices[bin_type] = {
                "global": np.zeros( (len(bins)-1,len(bins)-1) )
            }
            for groups in self.group_types:
                if len(groups) > 0:
                    spec = groups[0].spec
                    self.contact_matrices[bin_type][spec] = (
                        np.zeros( (len(bins)-1,len(bins)-1) )
                    )
            #self.contact_matrices[bin_type]["age_bins"] = bins

    def intitalise_contact_counters(self):
        self.contact_counts = {
            person.id: {
                spec: 0 for spec in self.contact_types
            } for person in self.world.people
        }

    def hash_ages(self):
        """store all ages and age_bin indexes in python dict for quick lookup"""
        self.age_idxs = {}
        for bin_type, bins in self.age_bins.items():    
            print(bin_type)
            self.age_idxs[bin_type] = {
                person.id: np.digitize(person.age, bins)-1 for person in self.world.people
            }
            self.ages = {person.id: person.age for person in self.world.people}

    def load_interactions(self, interaction_path=default_interaction_path):
        with open(interaction_path) as f:
            interaction_config = yaml.load(f, Loader=yaml.FullLoader)
            self.interaction_matrices = interaction_config["contact_matrices"]

            self.interaction_matrices["school"]["contacts"] = (
                Interaction.adapt_contacts_to_schools(
                    self.interaction_matrices["school"]["contacts"],
                    self.interaction_matrices["school"]["xi"],
                    age_min=0,
                    age_max=20,
                )
            )

    def generate_simulator(self,simulation_config_path=default_simulation_config_path):
        interaction = Interaction.from_file(
        #    config_filename=self.baseline_interaction_path, 
            population=self.world.people
        )
        health_index_generator = HealthIndexGenerator.from_file(
            asymptomatic_ratio=0.3
        )
        transmission_path = paths.configs_path / "defaults/transmission/XNExp.yaml"
        infection_selector = InfectionSelector.from_file(
            transmission_config_path=transmission_path,
            health_index_generator=health_index_generator,
        )
        oc = Observed2Cases.from_file(
            health_index_generator=infection_selector.health_index_generator,
            smoothing=True,
        )
        daily_cases_per_region = oc.get_regional_latent_cases()
        daily_cases_per_super_area = oc.convert_regional_cases_to_super_area(
            daily_cases_per_region, dates=["2020-02-28","2020-03-02"]
        )
        infection_seed = InfectionSeed(
            world=self.world,
            infection_selector=infection_selector,
            daily_super_area_cases=daily_cases_per_super_area,
            seed_strength=1.0,
            age_profile=None,
        )
        travel = Travel()
        policies = Policies.from_file()
        record = Record(
            record_path="out",
            record_static_data=True,
            #mpi_rank=mpi_rank,
        )
        leisure = generate_leisure_for_config(
            self.world,
        )
        self.simulator = Simulator.from_file(
            world=self.world,
            interaction=interaction,
            config_filename=simulation_config_path,
            leisure=leisure,
            travel=travel,
            infection_seed=None, #infection_seed,
            infection_selector=None, #infection_selector,
            policies=policies,
            record=record,
        )

    @staticmethod
    def _random_round(x):
        f = x % 1
        if np.random.uniform(0,1,1) < f:
            return int(x)+1
        else:
            return int(x)

    @staticmethod
    def _partition(n_contacts, m_groups):
        """
        Divide n_contacts evenly between m_groups, and randomly allocate the 'leftover'
        contacts to all groups
        """
        c = np.array([n_contacts//m_groups for _ in range(m_groups)])
        diff = n_contacts % m_groups

        idxs = np.random.randint(0,m_groups,diff)

        for idx in idxs:
            c[idx] += 1 # can't do as array operation. as repeat indices are ignored??   
        return c

    def get_active_subgroup(self, person: Person):
        active_subgroups = []
        subgroup_ids = []
        for subgroup in person.subgroups.iter():
            if subgroup is None or subgroup.group.spec == "commute_hub":
                continue
            if person in subgroup.people:
                subgroup_id = f"{subgroup.group.spec}_{subgroup.group.id}"
                if subgroup_id in subgroup_ids:
                    # gotcha: if you're receiving household visits, then you're active in residence
                    # and leisure -- but they are actually the same location...
                    continue
                active_subgroups.append( subgroup )
                subgroup_ids.append(subgroup_id)
        return active_subgroups

    def get_contacts_per_subgroup(self, subgroup_type, group: Group):
        """
        Get contacts that a person of subgroup type `subgroup_type` will have with each of the other subgroups,
        in a given group.
        eg. household has subgroups[subgroup_type]: kids[0], young_adults[1], adults[2], old[3]
        subgroup_type is the integer representing the type of person you're wanting to look at.
        
        """ 
        spec = group.spec
        matrix = self.interaction_matrices[spec]["contacts"]
        delta_t = self.simulator.timer.delta_time.seconds/3600.
        characteristic_time = self.interaction_matrices[spec]["characteristic_time"]
        if spec == "household":
            factor = delta_t / characteristic_time
            contacts_per_subgroup = [
                matrix[subgroup_type][ii]*factor for ii in range(len(group.subgroups))
            ]
        elif spec == "school":
            contacts_per_subgroup = [
                _get_contacts_in_school(matrix, group.years, subgroup_type, subgroup.subgroup_type )
                if len(subgroup.people) > 0 else 0 for subgroup in group.subgroups 
            ] 

        elif spec == "care_home":
            group_timings = [8.,24.,3.] # [wk,res,vis]
            factors = [
                min(time, group_timings[subgroup_type])/24. for time in group_timings
            ]
            contacts_per_subgroup = [
                matrix[subgroup_type][ii]*factors[ii] for ii in range(len(group.subgroups))
            ]
        elif spec == "company":
            contacts_per_subgroup = matrix[subgroup_type]
        else:
            contacts_per_subgroup = matrix[subgroup_type]
       
        return contacts_per_subgroup

    def simulate_1d_contacts(self, group: Group):
        for person in group.people:
            active_subgroups = self.get_active_subgroup(person)
            if len(active_subgroups) == 0:
                print(f"CHECK: person {person.id} active in NO subgroups!?")
                continue
            elif len(active_subgroups) > 1:
                print(f"CHECK: person {person.id} is in more than one subgroup!?")
                continue
            else:
                active_subgroup = active_subgroups[0]
                subgroup_idx = active_subgroup.subgroup_type # this is an INT.

            contacts_per_subgroup = self.get_contacts_per_subgroup(subgroup_idx, group)
            total_contacts = 0
            for subgroup_contacts, subgroup in zip(contacts_per_subgroup, group.subgroups):
                # potential contacts is one less if you're in that subgroup - can't contact yourself!
                is_same_subgroup = subgroup.subgroup_type == subgroup_idx
                potential_contacts = len(subgroup) - (is_same_subgroup)
                if potential_contacts == 0:
                    continue
                total_contacts = total_contacts + subgroup_contacts
                int_contacts = self._random_round(subgroup_contacts)

                contact_ids = []
                while len(contact_ids) < int_contacts:
                    contact = np.random.choice(subgroup)
                    if person.id != contact.id: # could loop forever without potential_contacts check.
                        contact_ids.append(contact.id)
                
                # For each type of contact matrix binning, eg BBC, polymod, SYOA...
                for bin_type in self.contact_matrices.keys():
                    age_idx = self.age_idxs[bin_type][person.id]
                    contact_age_idxs = [
                        self.age_idxs[bin_type][contact_id] for contact_id in contact_ids
                    ]

                    for cidx in contact_age_idxs:
                        self.contact_matrices[bin_type]["global"][age_idx,cidx] += 1
                        self.contact_matrices[bin_type][group.spec][age_idx,cidx] += 1
                
                # NOTE: self.contact_matrices["global"][age_idx, contact_age_idxs] += 1 # DOES NOT WORK for repeated ind
            
            self.contact_counts[person.id]["global"] += total_contacts
            if person.leisure == active_subgroup and person.leisure.group.spec == "household":
                contact_type = "household_visits"
            elif person.leisure == active_subgroup and person.leisure.group.spec == "care_home":
                contact_type = "care_home_visits"
            else:
                contact_type = group.spec
            self.contact_counts[person.id][contact_type] += total_contacts

    def simulate_network_contacts(self, group: Group):
        raise NotImplementedError

    def advance_step(self):
        print(self.simulator.timer.date)
        self.simulator.clear_world()
        delta_t = self.simulator.timer.delta_time.seconds / 3600.

        self.simulator.activity_manager.do_timestep()

        for group_type in self.group_types:
            for group in group_type:
                self.simulate_1d_contacts(group)

        next(self.simulator.timer)

    def save_tracker(self, overwrite=True):
        tracker = {
            "contact_counts" : self.contact_counts,
            "contact_matrices" : self.contact_matrices,
            "age_bins": self.age_bins,
            "simulation_days": self.simulation_days
        }

        if overwrite is False:
            if self.pickle_path.exists():
                pkldir = self.pickle_path.parent
                pkldir.mkdir(exist_ok=True,parents=True)
                stem = self.pickle_path.stem
                i = 1
                while self.pickle_path.exists():
                    self.pickle_path = pkldir / f"{stem}_{i}.pkl"
                    i = i+1
        with open(self.pickle_path,"wb+") as pkl:
            pickle.dump(tracker, pkl)

    def run_simulation(self, save_tracker=True):
        start_time = self.simulator.timer.date
        end_time = start_time + dt.timedelta(days=self.simulation_days)

        while self.simulator.timer.date < end_time:
            self.advance_step()

        if save_tracker:
            self.save_tracker()

        self.post_process_simulation()

    def convert_dict_to_df(self):
        self.contacts_df = pd.DataFrame.from_dict(self.contact_counts,orient="index")
        self.contacts_df["age"] = pd.Series(self.ages)
        for bins_type, age_idxes in self.age_idxs.items():
            col_name = f"{bins_type}_idx"
            self.contacts_df[col_name] = pd.Series(age_idxes)

    def calc_age_profiles(self):
        self.age_profiles = {}
        for bin_type in self.age_bins.keys():
            bins_idx = f"{bin_type}_idx"
            self.age_profiles[bin_type] = (
                self.contacts_df.groupby(self.contacts_df[bins_idx]).size().values
            )
        
    def calc_average_contacts(self):
        self.average_contacts = {}
        for bin_type in self.age_bins.keys():
            bins_idx = f"{bin_type}_idx"
            self.average_contacts[bin_type] = (
                self.contacts_df.groupby(self.contacts_df[bins_idx]).mean() / self.simulation_days
            )

    def normalise_contact_matrices(self):        
        for bin_type in self.age_bins.keys():
            matrices = self.contact_matrices[bin_type]
            age_profile = self.age_profiles[bin_type]
            
            for contact_type, mat in matrices.items():
                if contact_type == "age_bins":
                    continue
                mat = mat / (self.simulation_days*age_profile[np.newaxis, :])
                norm_mat = np.zeros( mat.shape )
                for i,row in enumerate(norm_mat):
                    for j,col in enumerate(norm_mat.T):
                        norm_factor = age_profile[i]/age_profile[j]
                        norm_mat[i,j] = (
                            0.5*(mat[i,j] + mat[j,i]*norm_factor)
                        )
                norm_mat[ norm_mat == 0. ] = np.nan
                self.contact_matrices[bin_type][contact_type] = norm_mat

    def process_contacts(self):
        self.convert_dict_to_df()
        self.calc_age_profiles()
        self.calc_average_contacts()
        self.normalise_contact_matrices()

    @staticmethod
    def _read_contact_data(contact_data_path):
        contact_data = pd.read_csv(contact_data_path)
        important_cols = np.array(["age_min", "age_max", "contacts"])
        mask = np.array([col in contact_data.columns for col in important_cols])
        if any(mask):
            print(f"{contact_data_path} missing col(s) {important_cols[mask]}")
        return contact_data

    def load_contact_data(
        self, 
        contact_data_paths=default_contact_data_paths
    ):
        if type(contact_data_paths) is dict:
            contact_data = {}
            for key, data_path in contact_data_paths.items():
                contact_data[key] = self._read_contact_data(data_path)
        else:
            contact_data = self._read_contact_data(contact_data_paths)
        self.contact_data = contact_data               

    def _plot_real_data(self, ax, data, **kwargs):
        endpoints = np.array(
            [x for x in data["age_min"].values] + [data["age_max"].values[-1]]
        )
        mids = 0.5*(endpoints[:-1] + endpoints[1:])
        widths = 0.5*(endpoints[1:] - endpoints[:-1])
        ydat = data["contacts"]
        ax.errorbar(mids, ydat, xerr=widths, **kwargs)

    def plot_stacked_contacts(
        self, bin_type="syoa", contact_types=None, plot_real_data=True
    ):
        f, ax = plt.subplots()

        average_contacts = self.average_contacts[bin_type]
        bins = self.age_bins[bin_type]
        lower = np.zeros(len(bins)-1)
        mids = 0.5*(bins[:-1]+bins[1:])
        widths = (bins[1:]-bins[:-1])
        plotted = 0

        if contact_types is None:
            contact_types = self.contact_types

        for ii, contact_type in enumerate(contact_types):
            print(average_contacts.columns)
            if contact_type not in average_contacts.columns:
                print(f"No contact_type {contact_type}")
                continue
            if contact_type == "global":
                continue

            if plotted > 6:
                hatch="/"
            else:
                hatch=None

            heights = average_contacts[contact_type]
            
            label = " ".join(x for x in contact_type.split("_")) # Avoids idiotic latex error.
            ax.bar(
                mids, heights, widths, bottom=lower,
                hatch=hatch, label=label
            )
            plotted += 1

            lower = lower + heights

        if plot_real_data:
            real_kwargs = {"marker":"x","ms":5, "lw":1, "color":"k"}
            line_styles=["-","--",":"]
            if type(self.contact_data) is dict:
                for ii,(key, data) in enumerate(self.contact_data.items()):
                    self._plot_real_data(
                        ax, data, label=key, **real_kwargs, ls=line_styles[ii]
                    )
            else:
                self.plot_real_data(
                    ax, self.contact_data, label="real", **real_kwargs, ls=line_styles[ii]
                )

        ax.set_xlim(bins[0], bins[-1])

        ax.legend(bbox_to_anchor = (0.5,1.02),loc='lower center',ncol=3)
        ax.set_xlabel('Age')
        ax.set_ylabel('average contacts per day')
        #f.subplots_adjust(top=0.9)
        return ax 
        
    def plot_contact_matrix(self, bin_type="bbc", contact_type="school", **kwargs):

        bins = self.age_bins[bin_type]

        if len(bins) < 25:
            labels = [
                f"{low}-{high-1}" for low,high in zip(bins[:-1], bins[1:])
            ]
        else:
            labels = None
        mat = self.contact_matrices[bin_type][contact_type]

        cmap = cm.get_cmap('RdYlBu_r')
        cmap.set_bad(color="lightgrey")

        f, ax = plt.subplots()
        im = ax.imshow(mat.T,origin='lower',cmap=cmap,vmin=0.)
        if labels is not None:
            ax.set_xticks(np.arange(len(mat)))
            ax.set_xticklabels(labels,rotation=90)
            ax.set_yticks(np.arange(len(mat)))
            ax.set_yticklabels(labels)
        f.colorbar(im)
        ax.set_title(f"{bin_type} binned contacts in {contact_type}")
        return ax


if __name__ == "__main__":
    world_path = "../../../scripts/tests.hdf5"
    world = generate_world_from_hdf5(world_path)

    max_age = 100
    bbc_bins = np.array([0,5,10,13,15,18,20,22,25,30,35,40,45,50,55,60,65,70,75,max_age])

    age_bins = {"bbc": bbc_bins, "5yr": np.arange(0,105,5)}
    contact_types = [
        "household", "school", "grocery", "household_visits", "pub", "university", 
        "company", "city_transport", "inter_city_transports", "care_home", 
        "care_home_visits", "cinema", "hospital",
    ]

    #ct_plots = ContactTracker(world, age_bins=age_bins)
    #ct_plots.generate_simulator()
    #ct_plots.run_simulation()
    ct_plots = ContactTracker.from_pickle(world)
    ct_plots.process_contacts()
    

    relevant_contact_types = ["household", "school", "company"]
    relevant_bin_types = ["bbc","syoa"]
    for rbt in relevant_bin_types:  
        plot_dir = Path(f"./plots/")  
        plot_dir.mkdir(exist_ok=True, parents=True)
        stacked_contacts_plot = ct_plots.plot_stacked_contacts(
            bin_type="bbc", contact_types=contact_types
        )
        stacked_contacts_plot.plot()
        plt.savefig(plot_dir / f"{rbt}_contacts.png", dpi=150, bbox_inches='tight')
        mat_dir = plot_dir / f"{rbt}"
        mat_dir.mkdir(exist_ok=True, parents=True)
        for rct in relevant_contact_types:
            mat_plot = ct_plots.plot_contact_matrix(
                bin_type=rbt, contact_type=rct
            )
            mat_plot.plot()
            plt.savefig(mat_dir / f"{rct}.png", dpi=150, bbox_inches='tight')

    plt.show()




