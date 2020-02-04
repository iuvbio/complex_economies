
import logging
from copy import copy, deepcopy
from pprint import pformat

from mesa import Model
from mesa.datacollection import DataCollector

from complex_economies.agents import CapitalGoodFirm, ConsumptionGoodFirm
from complex_economies.schedule import GroupedActivation
from complex_economies.utils import messages
from complex_economies.utils.misc import d


def compute_gdp(model):
    consumption_firms = model.get_group('consumption_firm')
    capital_firms = model.get_group('capital_firm')
    return (
        sum([f.sales * f.price for f in consumption_firms])
        + sum([f.output * f.price for f in capital_firms])
    )


class ComplexEconomy(Model):

    log = logging.getLogger(__name__)
    groups = ['consumption_firm', 'capital_firm']
    stages = ['stage_one', 'stage_two', 'stage_three', 'stage_four', 'stage_five']
    stage_functions = {
        'stage_one': [
            'update_average_prices',
            'update_avg_ulc',
            'update_average_labour_productivity',
            'update_sector_competitiveness'
        ],
        'stage_two': [
            'aggregate_investment'
        ],
        'stage_three': [
            'update_labour_supply',
            'aggregate_labour_demand',
            'update_employment',
            'update_market_wage',
            'update_consumption'
        ],
        'stage_four': [
            'aggregate_production',
            'aggregate_inventories',
        ],
        'stage_five': [
            'exit_and_entry'
        ]
    }

    avg_cap_price = d(0)
    avg_comp_competitiveness = d(0)
    avg_cap_competitiveness = d(0)
    average_unit_labour_cost = d(0)
    labour_demand = d(0)
    unemployment = d(0)
    delta_cpi = d(0)
    delta_productivity = d(0)
    delta_unemployment = d(0)
    expansion_investment = d(0)
    replacement_investment = d(0)
    agg_inventories = d(0)
    agg_production = d(0)

    def __init__(self, parameters, market_wage, cpi, avg_labour_productivity,
                 liquid_assets, capital_stock, labour_supply, seed=None):
        self.schedule = GroupedActivation(
            self, self.groups, self.stages,
            interim_functions=self.stage_functions
        )
        self.datacollector = DataCollector(
            model_reporters={
                'market_wage': 'market_wage',
                'consumption': 'consumption',
                'expansion_investment': 'expansion_investment',
                'replacement_investment': 'replacement_investment',
                'investment': 'investment',
                'inventories': 'agg_inventories',
                'production': 'agg_production',
                'cpi': 'cpi',
                'avg_cap_price': 'avg_cap_price',
                'avg_labour_prod': 'avg_labour_prod',
                'labour_supply': 'labour_supply',
                'labour_demand': 'labour_demand',
                'employment': 'employment',
                'unemployment': 'unemployment',
                'avg_comp_competitiveness': 'avg_comp_competitiveness',
                'avg_cap_competitiveness': 'avg_cap_competitiveness',
                'gdp': compute_gdp
            },
            tables={
                'consumption_firm': {
                    'step': [],
                    'agent_id': [],
                    'competitiveness': [],
                    'expected_demand': [],
                    'market_share': [],
                    'demand': [],
                    'desired_production': [],
                    'desired_capital_stock': [],
                    'labour_demand': [],
                    'desired_ei': [],
                    'desired_ri': [],
                    'supplier': [],
                    'expansion_investment': [],
                    'replacement_investment': [],
                    'production': [],
                    'output': [],
                    'inventory': [],
                    'sales': [],
                    'profit': [],
                    'liquid_assets': [],
                    'capital_stock': [],
                    'debt_stock': [],
                    'price': [],
                    'upc': [],
                    'average_productivity': [],
                    'available_debt': [],
                    'bankrupt': []
                },
                'capital_firm': {
                    'step': [],
                    'agent_id': [],
                    'competitiveness': [],
                    'demand': [],
                    'production': [],
                    'labour_demand': [],
                    'output': [],
                    'sales': [],
                    'profit': [],
                    'liquid_assets': [],
                    'debt_stock': [],
                    'market_share': [],
                    'machine_generation': [],
                    'price': [],
                    'upc': [],
                    'labour_productivity': [],
                    'available_debt': [],
                    'bankrupt': []
                }
            }
        )
        self.innovation = parameters['innovation']
        self.social_policy = parameters['social_policy']
        self.inventory_deprecation = parameters['inventory_deprecation']

        # parameters
        n_consumption_firms = parameters['n_consumption_firms']
        n_capital_firms = parameters['n_capital_firms']
        self.replicator_dynamics_coeff = parameters['replicator_dynamics_coeff']
        self.competitiveness_weights = parameters['competitiveness_weights']
        self.distribution_bounds = parameters['distribution_bounds']
        self.labour_supply_growth = parameters['labour_supply_growth']
        self.wage_setting = parameters['wage_setting']
        self.desired_capital_utilization = parameters['desired_capital_utilization']
        self.trigger_rule = parameters['trigger_rule']
        self.payback_period_parameter = parameters['payback_period_parameter']
        self.mark_up = parameters['mark_up']
        self.interest_rate = parameters['interest_rate']
        self.wage_share = parameters['wage_share']
        self.betas = parameters['betas']
        # NOTE: max_debt_sales_ratio is not specified in the paper
        self.max_debt_sales_ratio = parameters['max_debt_sales_ratio']

        # initial conditions
        self.market_wage = market_wage
        self.cpi = cpi
        self.avg_labour_prod = avg_labour_productivity
        self.labour_supply = labour_supply

        # computed
        self.max_capital_labour_share = (
            n_capital_firms / (n_consumption_firms + n_capital_firms)
        )
        init_market_share = (1 / n_consumption_firms, 1 / n_capital_firms)
        self.employment = labour_supply
        self.consumption = self.compute_consumption()
        self.unemployment_rate = self.unemployment / self.employment

        # create capital good firms
        for i in range(int(n_capital_firms)):
            f = CapitalGoodFirm(i, self, liquid_assets, init_market_share[1])
            self.schedule.add(f)
        # create consumption good firms
        supplier = 0
        for i in range(int(n_consumption_firms)):
            if parameters['fix_supplier']:
                if i % 4 == 0 and i != 0:
                    supplier += 1
            else:
                supplier = None
            f = ConsumptionGoodFirm(
                i + n_capital_firms, self, liquid_assets, capital_stock,
                init_market_share[0], supplier=supplier
            )
            self.schedule.add(f)

        self.running = True
        self.datacollector.collect(self)

        self.log.info(messages.model_init_message.format(
            wage=self.market_wage,
            cpi=self.cpi,
            avg_labour_prod=self.avg_labour_prod,
            labour_supply=self.labour_supply,
            comp_market_share=init_market_share[0],
            cap_market_share=init_market_share[1],
            employment=self.employment,
            consumption=self.consumption,
            unemployment_rate=self.unemployment_rate,
            parameters=pformat(parameters)
        ))

    @property
    def investment(self):
        return self.expansion_investment + self.replacement_investment

    @property
    def max_capital_labour(self):
        return self.max_capital_labour_share * self.labour_supply

    @property
    def capital_labour_demand(self):
        capital_firms = self.get_group('capital_firm')
        return sum([a.labour_demand for a in capital_firms])

    def get_group(self, group, include_bankrupt=False):
        firms = [
            a for a in self.schedule.agents if a.group == group
        ]
        if include_bankrupt:
            return firms
        return [f for f in firms if not f.bankrupt]

    def compute_average_price(self, group, weighted=False):
        firms = self.get_group(group)
        if not weighted:
            prices = [firm.price for firm in firms]
            return sum(prices) / len(prices)
        return sum([firm.market_share * firm.price for firm in firms])

    def update_average_prices(self):
        cpi = self.compute_average_price('consumption_firm')
        self.delta_cpi = (cpi - self.cpi) / self.cpi
        self.cpi = cpi
        self.avg_cap_price = self.compute_average_price('capital_firm')

    def update_avg_ulc(self):
        capital_firms = self.get_group('capital_firm')
        lpcs = [a.machine.compute_unit_labour_cost(self) for a in capital_firms]
        self.average_unit_labour_cost = sum(lpcs) / len(lpcs)

    def update_average_labour_productivity(self):
        firms = self.get_group('capital_firm')
        avg_labour_prod = (
            sum([f.machine.labour_productivity_coefficient for f in firms])
            / len(firms)
        )
        self.delta_productivity = (avg_labour_prod - self.avg_labour_prod) / self.avg_labour_prod
        self.avg_labour_prod = avg_labour_prod

    def compute_sector_competitiveness(self, group):
        firms = self.get_group(group)
        comp = sum([
            f.competitiveness * f.market_share for f in firms
        ])
        return round(comp, 2)

    def update_sector_competitiveness(self):
        self.avg_comp_competitiveness = self.compute_sector_competitiveness('consumption_firm')
        self.avg_cap_competitiveness = self.compute_sector_competitiveness('capital_firm')

    def aggregate_investment(self):
        firms = self.get_group('consumption_firm')
        self.expansion_investment = sum([
            f.expansion_investment for f in firms
        ])
        self.replacement_investment = sum([
            f.replacement_investment for f in firms
        ])

    def update_labour_supply(self):
        self.labour_supply = (
            self.labour_supply * (1 + self.labour_supply_growth)
        )

    def aggregate_labour_demand(self):
        self.labour_demand = sum([
            a.labour_demand for a in self.schedule.agents
        ])

    def update_employment(self):
        self.employment = min(self.labour_demand, self.labour_supply)
        self.unemployment = max(0, self.labour_supply - self.employment)
        unemployment_rate = self.unemployment / self.labour_supply
        delta_unemployment = unemployment_rate - self.unemployment_rate
        self.unemployment_rate = unemployment_rate
        self.delta_unemployment = delta_unemployment

    def update_market_wage(self):
        psi1 = self.wage_setting['cpi_weight']
        psi2 = self.wage_setting['avg_lp_weight']
        psi3 = self.wage_setting['unemployment_weight']
        self.market_wage = (
            self.market_wage * (  # NOTE: in the paper, this is +
                1 + psi1 * self.delta_cpi
                + psi2 * self.delta_productivity
                + psi3 * self.delta_unemployment
            )
        )

    def compute_consumption(self):
        household_consumption = self.market_wage * self.employment
        if self.social_policy == 'welfare':
            government_consumption = self.wage_share * self.unemployment
        else:
            government_consumption = (
                self.wage_share * self.market_wage * self.labour_supply
            )
        return household_consumption + government_consumption

    def update_consumption(self):
        self.consumption = self.compute_consumption()

    def aggregate_production(self):
        firms = self.get_group('consumption_firm')
        self.agg_production = sum([
            f.production for f in firms
        ])

    def aggregate_inventories(self):
        firms = self.get_group('consumption_firm')
        self.agg_inventories = sum([
            f.inventory for f in firms
        ])

    def exit_and_entry(self):  # TODO: adjust market share after re-entry
        for group in self.groups:
            self.log.info(f'entry and exit for group {group}')
            firms = self.get_group(group)
            dead_firms = [
                f for f in firms
                if f.market_share <= 0 or f.liquid_assets < 0
            ]
            self.log.info(f'Bankrupt firms: {len(dead_firms)}')
            alive_firms = [
                f for f in firms if f not in dead_firms
            ]
            for firm in dead_firms:
                firm.bankrupt = True
                copy_firm = self.random.choice(alive_firms)
                next_id = max([a.unique_id for a in self.schedule.agents]) + 1
                assets = copy_firm.liquid_assets
                market_share = copy_firm.market_share
                capital_stock = copy_firm.capital_stock if group == 'consumption_firm' else None
                constructor = {
                    'consumption_firm': ConsumptionGoodFirm,
                    'capital_firm': CapitalGoodFirm
                }.get(group)
                new_firm = constructor(
                    next_id, self, assets,
                    market_share=market_share, capital_stock=capital_stock
                )
                if group == 'capital_firm':
                    new_firm.machine.labour_productivity_coefficient = copy_firm.machine.labour_productivity_coefficient
                    new_firm.machine.price = copy_firm.machine.price
                self.schedule.add(new_firm)

    def distribute_leftover_machines(self):
        pass

    def step(self):
        # run all stages of a step
        self.schedule.step()
        # collect data
        self.datacollector.collect(self)
