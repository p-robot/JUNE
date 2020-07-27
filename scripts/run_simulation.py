import time

from june.world import generate_world_from_hdf5
from june.hdf5_savers import load_geography_from_hdf5
from june.demography.geography import Geography
from june.interaction import Interaction, ContactAveraging
from june.infection import Infection
from june.infection.health_index import HealthIndexGenerator
from june.infection.transmission import TransmissionConstant
from june.groups import Hospitals, Schools, Companies, Households, CareHomes, Cemeteries
from june.groups.leisure import Cinemas, Pubs, Groceries, generate_leisure_for_config
from june.simulator import Simulator
from june.infection_seed import InfectionSeed
from june.policy import Policies
from june import paths
from june.infection.infection import InfectionSelector
from june.groups.commute import *

world_file = "./tests.hdf5"
config_path = "./config.yaml"

world = generate_world_from_hdf5(world_file, chunk_size=1_000_000)
print("World loaded succesfully")

# regenerate lesiure
leisure = generate_leisure_for_config(world, config_path)

# health index and infection selecctor 
health_index_generator = HealthIndexGenerator.from_file(asymptomatic_ratio=0.2)
infection_selector = InfectionSelector.from_file(health_index_generator=health_index_generator)

# interaction
interaction = ContactAveraging.from_file(selector=infection_selector)

# initial infection seeding
infection_seed = InfectionSeed(
    world.super_areas, infection_selector,
)

infection_seed.unleash_virus(50) # number of initial cases

# policies
policies = Policies.from_file()

# create simulator

simulator = Simulator.from_file(
    world=world,
    policies=policies,
    interaction=interaction,
    leisure=leisure,
    config_filename=config_path,
    save_path="results",
)
print("simulator ready to go")

t1 = time.time()
simulator.run()
t2 = time.time()

print(f" Simulation took {t2-t1} seconds")

