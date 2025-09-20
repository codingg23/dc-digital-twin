"""
thermal.py

Simplified thermal model for data centre zones.

The physics here is a mix of first-principles and empirical approximations.
I am not a thermodynamics PhD. Calibrate against real data before trusting
any numbers that come out of this.

Key relationships being modelled:
  1. Rack outlet temp = inlet temp + (power_kw / airflow_kg_per_s / specific_heat)
     → the 'sensible heat equation' for air cooling
  2. Hot aisle temp = weighted average of rack outlet temps in the row
  3. Inlet temp = f(CRAC supply temp, hot aisle recirculation fraction)
     → recirculation is the main thing that goes wrong in poorly managed aisles
  4. CRAC load = heat load it needs to remove from its zone

The recirculation model is really rough. In practice it depends heavily
on physical blanking panel coverage, containment, and airflow bypasses.
TODO: make recirculation configurable per zone.

References I found useful:
  - ASHRAE TC 9.9 data centre guidelines
  - "Fundamentals of Data Centre Cooling" — Vali et al.
  - Various white papers from Schneider/Vertiv (take with a grain of salt)
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional


# Physical constants
SPECIFIC_HEAT_AIR = 1.006  # kJ/(kg·K) at ~25°C
AIR_DENSITY_KGPM3 = 1.2   # kg/m³ at sea level, ~20°C


@dataclass
class ZoneThermalConfig:
    zone_id: str
    n_racks: int = 20
    crac_supply_temp_c: float = 16.0       # CRAC discharge temp
    crac_airflow_m3_per_s: float = 3.0     # per CRAC unit
    n_crac_units: int = 2
    recirculation_fraction: float = 0.08   # fraction of hot air that recirculates
    target_inlet_c: float = 21.0
    containment: bool = True               # hot aisle containment


@dataclass
class RackThermalState:
    rack_id: str
    power_kw: float
    inlet_temp_c: float = 21.0
    outlet_temp_c: float = 0.0    # will be computed
    airflow_kg_per_s: float = 0.5  # typical 1U server = ~0.05 m³/s, rack has ~10 servers

    def __post_init__(self):
        if self.outlet_temp_c == 0.0:
            self.outlet_temp_c = self.compute_outlet_temp()

    def compute_outlet_temp(self) -> float:
        """
        Sensible heat equation: Q = m_dot * Cp * delta_T
        Solving for delta_T: delta_T = Q / (m_dot * Cp)

        Q in kW, m_dot in kg/s, Cp in kJ/(kg·K)
        delta_T comes out in °C.
        """
        if self.airflow_kg_per_s <= 0:
            return self.inlet_temp_c + 99.0  # something has gone very wrong

        delta_t = self.power_kw / (self.airflow_kg_per_s * SPECIFIC_HEAT_AIR)
        return self.inlet_temp_c + delta_t

    @property
    def delta_t(self) -> float:
        return self.outlet_temp_c - self.inlet_temp_c


class ZoneThermalModel:
    """
    Models thermal behaviour for a single zone (e.g., a row or set of rows
    served by one CRAC unit).

    A 'zone' here is just a set of racks + the CRAC units that cool them.
    The coupling is: rack load → hot aisle temp → CRAC load → CRAC performance.
    """

    def __init__(self, config: ZoneThermalConfig):
        self.config = config

    def total_crac_airflow_kg_per_s(self) -> float:
        """Total cooling airflow from all CRAC units in the zone."""
        airflow_m3 = self.config.crac_airflow_m3_per_s * self.config.n_crac_units
        return airflow_m3 * AIR_DENSITY_KGPM3

    def compute_hot_aisle_temp(self, rack_states: list[RackThermalState]) -> float:
        """
        Hot aisle temp is the airflow-weighted average of rack outlet temps.
        In reality it varies along the aisle, but this gives a representative value.
        """
        if not rack_states:
            return self.config.crac_supply_temp_c + 10.0  # fallback

        total_flow = sum(r.airflow_kg_per_s for r in rack_states)
        if total_flow == 0:
            return self.config.crac_supply_temp_c + 10.0

        weighted_temp = sum(r.outlet_temp_c * r.airflow_kg_per_s for r in rack_states)
        return weighted_temp / total_flow

    def compute_inlet_temp(self, hot_aisle_temp: float) -> float:
        """
        Cold aisle inlet temp = mix of CRAC supply and hot aisle recirculation.
        With good containment, recirculation is low (5-10%).
        Bad containment can push it to 20-30%+.
        """
        recirculation = self.config.recirculation_fraction
        if self.config.containment:
            recirculation *= 0.6   # containment cuts recirculation significantly

        inlet = (
            (1 - recirculation) * self.config.crac_supply_temp_c
            + recirculation * hot_aisle_temp
        )
        return inlet

    def compute_crac_load_kw(self, rack_states: list[RackThermalState]) -> float:
        """
        CRAC load = total sensible heat removed from the zone.
        Should equal total IT load in steady state (conservation of energy),
        but slightly higher due to lighting, PDU losses, etc.
        We approximate as IT_load * 1.05 (5% overhead).
        """
        it_load = sum(r.power_kw for r in rack_states)
        return it_load * 1.05

    def crac_capacity_fraction(self, rack_states: list[RackThermalState]) -> float:
        """Fraction of total CRAC capacity being used. >0.85 is getting risky."""
        crac_load = self.compute_crac_load_kw(rack_states)

        # CRAC nominal capacity: airflow * density * Cp * delta_T across coil
        # Assume CRAC coil delta_T ≈ 12°C (supply temp 16°C, return ~28°C)
        coil_delta_t = 12.0
        crac_capacity_kw = (
            self.total_crac_airflow_kg_per_s() * SPECIFIC_HEAT_AIR * coil_delta_t
        )
        return crac_load / max(crac_capacity_kw, 0.001)

    def steady_state(self, rack_powers_kw: list[float]) -> dict:
        """
        Compute steady-state thermal conditions given rack power loads.

        Returns a dict with per-rack and zone-level results.
        """
        n = len(rack_powers_kw)
        # per-rack airflow: assume proportional to power with a floor
        # real fan laws are more complex but this is good enough
        airflows = [
            max(0.3, p / 20.0 * 0.8)  # 0.8 kg/s at 20kW, floor at 0.3
            for p in rack_powers_kw
        ]

        # initial inlet = CRAC supply (first pass, no recirculation info yet)
        inlet = self.config.crac_supply_temp_c + 2.0  # 2°C rise in cold aisle

        # iterate to convergence (usually 3-5 iterations)
        for _ in range(10):
            rack_states = [
                RackThermalState(
                    rack_id=f"rack_{i}",
                    power_kw=rack_powers_kw[i],
                    inlet_temp_c=inlet,
                    airflow_kg_per_s=airflows[i],
                )
                for i in range(n)
            ]
            # update rack outlet temps
            for rs in rack_states:
                rs.outlet_temp_c = rs.compute_outlet_temp()

            hot_aisle = self.compute_hot_aisle_temp(rack_states)
            new_inlet = self.compute_inlet_temp(hot_aisle)

            if abs(new_inlet - inlet) < 0.01:
                break
            inlet = new_inlet

        return {
            "inlet_temp_c": round(inlet, 2),
            "hot_aisle_temp_c": round(hot_aisle, 2),
            "crac_load_kw": round(self.compute_crac_load_kw(rack_states), 1),
            "crac_capacity_fraction": round(self.crac_capacity_fraction(rack_states), 3),
            "per_rack": [
                {
                    "rack_id": rs.rack_id,
                    "power_kw": rs.power_kw,
                    "inlet_c": round(rs.inlet_temp_c, 2),
                    "outlet_c": round(rs.outlet_temp_c, 2),
                    "delta_t_c": round(rs.delta_t, 2),
                }
                for rs in rack_states
            ],
        }
