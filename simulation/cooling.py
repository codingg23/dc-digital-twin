"""
cooling.py

CRAC/chiller plant models for the digital twin.

COP (Coefficient of Performance) of a chiller varies with:
  - Load fraction (part-load efficiency)
  - Outdoor wet-bulb temperature (drives condenser performance)
  - Chilled water supply temperature setpoint

This is important for PUE modelling  -  a chiller at 40% load
might have better or worse COP than at 80% depending on the
equipment curve. Scroll down to ChillerPlant for details.
"""
import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class CRACConfig:
    unit_id: str
    nominal_capacity_kw: float = 80.0
    nominal_airflow_m3_per_s: float = 3.0
    supply_temp_setpoint_c: float = 16.0
    # efficiency drops at part load and high return temp
    rated_cop: float = 3.2


class CRACUnit:
    def __init__(self, config: CRACConfig):
        self.config = config
        self._enabled = True

    def disable(self):
        """Simulate a CRAC failure."""
        self._enabled = False

    def enable(self):
        self._enabled = True

    @property
    def available_capacity_kw(self) -> float:
        return self.config.nominal_capacity_kw if self._enabled else 0.0

    def actual_cop(self, load_fraction: float, return_temp_c: float) -> float:
        """
        Part-load COP model. Most CRAC units are least efficient at very
        low loads (compressor cycling) and near full load (reduced headroom).
        Sweet spot is usually 60-80% load.

        Also degrades as return temp rises beyond the design point.
        """
        if not self._enabled:
            return 0.0

        load_fraction = float(np.clip(load_fraction, 0.1, 1.0))

        # part-load curve  -  rough quadratic approximation
        plr_factor = -0.8 * (load_fraction - 0.75) ** 2 + 1.0
        plr_factor = float(np.clip(plr_factor, 0.6, 1.0))

        # return temp penalty
        design_return_temp = 28.0  # °C
        temp_delta = return_temp_c - design_return_temp
        temp_factor = 1.0 - 0.02 * max(temp_delta, 0)  # 2% COP drop per °C above design

        return self.config.rated_cop * plr_factor * temp_factor

    def power_consumption_kw(self, load_kw: float, return_temp_c: float = 28.0) -> float:
        """Electrical power draw of the CRAC unit at given cooling load."""
        if not self._enabled or load_kw <= 0:
            return 0.0
        load_fraction = load_kw / self.config.nominal_capacity_kw
        cop = self.actual_cop(load_fraction, return_temp_c)
        return load_kw / max(cop, 0.1)


@dataclass
class ChillerPlantConfig:
    n_chillers: int = 2
    chiller_capacity_kw: float = 2000.0  # per chiller
    design_cop: float = 5.5              # modern centrifugal chiller
    # cooling tower wet-bulb design point
    design_wetbulb_c: float = 19.0


class ChillerPlant:
    """
    Central chiller plant model.

    In a typical data centre:
      IT load → CRAC units → chilled water loop → chillers → cooling towers

    The chiller COP is what really drives PUE. A chiller running at
    design conditions might hit COP 5.5+, but at high ambient temps
    or part load it can drop significantly.

    This is simplified  -  real chiller control has sequencing logic,
    lead-lag rotation, demand limiting, etc. Ignoring all that here.
    """

    def __init__(self, config: ChillerPlantConfig):
        self.config = config

    def total_capacity_kw(self, n_online: Optional[int] = None) -> float:
        n = n_online or self.config.n_chillers
        return n * self.config.chiller_capacity_kw

    def cop_at_conditions(self, load_fraction: float, outdoor_wetbulb_c: float) -> float:
        """
        Chiller COP as a function of load and outdoor wet-bulb temperature.

        Higher wet-bulb = worse condenser performance = lower COP.
        This is why data centres in cooler climates have better PUE.

        Approach: start from design COP, apply corrections.
        """
        load_fraction = float(np.clip(load_fraction, 0.1, 1.0))

        # wet-bulb correction: ~3% COP drop per °C above design point
        wb_delta = outdoor_wetbulb_c - self.config.design_wetbulb_c
        wb_factor = 1.0 - 0.03 * max(wb_delta, 0)

        # part-load: centrifugal chillers are usually most efficient at 70-80% load
        # IPLV curves from ASHRAE 90.1
        if load_fraction >= 0.75:
            plr_factor = 1.0 - 0.15 * (load_fraction - 0.75) / 0.25
        elif load_fraction >= 0.5:
            plr_factor = 1.0
        else:
            plr_factor = 0.85 + 0.15 * (load_fraction / 0.5)

        return self.config.design_cop * wb_factor * plr_factor

    def plant_power_kw(self, cooling_load_kw: float, outdoor_wetbulb_c: float = 19.0) -> float:
        """Total electrical power of the chiller plant at given cooling load."""
        if cooling_load_kw <= 0:
            return 0.0
        cap = self.total_capacity_kw()
        load_frac = cooling_load_kw / cap
        cop = self.cop_at_conditions(load_frac, outdoor_wetbulb_c)

        # add cooling tower fan power (~5% of heat rejected)
        heat_rejected = cooling_load_kw * (1 + 1/cop)
        tower_power = heat_rejected * 0.012   # typical tower fan power ratio

        return (cooling_load_kw / cop) + tower_power
