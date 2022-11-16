# Copyright 2022 Digital Brain Laboratory, Yan Song and He jiang
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from typing import OrderedDict
from light_malib.registry import registry
from light_malib.agent.agent_manager import AgentManager

from light_malib.agent.policy_data.policy_data_manager import PolicyDataManager
from light_malib.utils.logger import Logger
from light_malib.agent import Population
from light_malib.utils.desc.task_desc import TrainingDesc
import numpy as np
import importlib


class PSROScheduler:
    """
    TODO(jh): abstract it later
    """

    def __init__(
        self, cfg, agent_manager: AgentManager, policy_data_manager: PolicyDataManager
    ):
        self.cfg = cfg
        self.agent_manager = agent_manager
        self.agents = self.agent_manager.agents
        self.population_id = "default"
        self.policy_data_manager = policy_data_manager
        self.meta_solver_type = self.cfg.get("meta_solver", "nash")
        self.sync_training = self.cfg.get("sync_training", False)

        Logger.warning("use meta solver type: {}".format(self.meta_solver_type))
        solver_module = importlib.import_module(
            "light_malib.framework.meta_solver.{}".format(self.meta_solver_type)
        )
        self.meta_solver = solver_module.Solver()
        self._schedule = self._gen_schedule()

    def initialize(self, populations_cfg):
        # add populations
        for agent_id in self.agents.training_agent_ids:
            assert len(populations_cfg) == 1
            population_id = populations_cfg[0]["population_id"]
            assert population_id == self.population_id
            algorithm_cfg = populations_cfg[0]["algorithm"]
            self.agent_manager.add_new_population(
                agent_id, self.population_id, algorithm_cfg
            )

        for population_cfg in populations_cfg:
            population_id = population_cfg["population_id"]
            algorithm_cfg = population_cfg.algorithm
            policy_init_cfg = algorithm_cfg.get("policy_init_cfg", None)
            if policy_init_cfg is None:
                continue
            for agent_id, agent_policy_init_cfg in policy_init_cfg.items():
                agent_initial_policies = agent_policy_init_cfg.get(
                    "initial_policies", None
                )
                if agent_initial_policies is None:
                    continue
                for policy_cfg in agent_initial_policies:
                    policy_id = policy_cfg["policy_id"]
                    policy_dir = policy_cfg["policy_dir"]
                    self.agent_manager.load_policy(
                        agent_id, population_id, policy_id, policy_dir
                    )
                    Logger.info(f"Load initial policy {policy_id} from {policy_dir}")

        # generate the first policy
        for agent_id in self.agents.training_agent_ids:
            self.agent_manager.gen_new_policy(agent_id, self.population_id)

        # TODO(jh):Logger
        Logger.warning("after initialization:\n{}".format(self.agents))

    def _gen_schedule(self):
        max_generations = self.cfg.max_generations
        for generation_ctr in range(max_generations):
            for training_agent_id in self.agents.training_agent_ids:
                # get all available policy_ids from the population
                agent_id2policy_ids = OrderedDict()
                agent_id2policy_indices = OrderedDict()
                for agent_id in self.agents.keys():
                    population: Population = self.agents[agent_id].populations[
                        self.population_id
                    ]
                    agent_id2policy_ids[agent_id] = population.policy_ids
                    agent_id2policy_indices[agent_id] = np.array(
                        [
                            self.agents[agent_id].policy_id2idx[policy_id]
                            for policy_id in population.policy_ids
                        ]
                    )

                # get payoff matrix
                payoff_matrix = self.policy_data_manager.get_matrix_data(
                    "payoff", agent_id2policy_indices
                )

                # compute nash
                equlibrium_distributions = self.meta_solver.compute(payoff_matrix)

                policy_distributions = {}
                for probs, (agent_id, policy_ids) in zip(
                    equlibrium_distributions, agent_id2policy_ids.items()
                ):
                    policy_distributions[agent_id] = OrderedDict(zip(policy_ids, probs))

                # gen new policy
                training_policy_id = self.agent_manager.gen_new_policy(
                    agent_id, self.population_id
                )
                policy_distributions[training_agent_id] = {training_policy_id: 1.0}

                Logger.warning(
                    "********** Generation[{}] Agent[{}] START **********".format(
                        generation_ctr, training_agent_id
                    )
                )

                stopper = registry.get(registry.STOPPER, self.cfg.stopper.type)(
                    policy_data_manager=self.policy_data_manager,
                    **self.cfg.stopper.kwargs,
                )

                training_desc = TrainingDesc(
                    training_agent_id,
                    training_policy_id,
                    policy_distributions,
                    self.agents.share_policies,
                    self.sync_training,
                    stopper,
                )
                yield training_desc

    def get_task(self):
        try:
            task = next(self._schedule)
            return task
        except StopIteration:
            return None

    def submit_result(self, result):
        pass
