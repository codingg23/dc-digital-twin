"""
dc_model.py

Top-level digital twin model. Orchestrates power, thermal, and cooling sub-models.

A 'DCModel' represents a floor or a zone of a data centre.
You give it a layout config + load profile, it tells you
what the thermal and power conditions will be.

Currently stateless — each call to run() computes a steady-state result.
Time-series simulation (step through load changes over time) is on the TODO list.
"""

import yaml
import logging
from dataclasses import dataclass, field
from typing import Optional

from .thermal import ZoneThermalModel, ZoneThermalConfig

logger = logging.getLogger(__name__)


@dataclass
class RackSpec:
    rack_id: str
    row_id: str
    max_kw: float = 20.0
    current_kw: float = 0.0    # set at run time


@dataclass
class ZoneSpec:
    zone_id: str
    row_ids: list[str] = field(default_factory=list)
    n_crac_units: int = 2
    crac_airflow_m3_per_s: float = 3.0
    crac_supply_temp_c: float = 16.0
    containment: bool = True


@dataclass
class FloorLayout:
    floor_id: str
    zones: list[ZoneSpec] = field(default_factory=list)
    racks: list[RackSpec] = field(default_factory=list)
    total_ups_capacity_kw: float = 5000.0


@dataclass
class SimResult:
    floor_id: str
    total_it_load_kw: float
    total_facility_load_kw: float   # IT + cooling + lighting overhead
    pue: float
    zone_results: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def print_summary(self):
        print(f"\nSimulation result: {self.floor_id}")
        print("─" * 50)
        print(f"  IT load:          {self.total_it_load_kw:.0f} kW")
        print(f"  Facility load:    {self.total_facility_load_kw:.0f} kW")
        print(f"  PUE:              {self.pue:.3f}")

        for zone_id, zone_data in self.zone_results.items():
            print(f"\n  Zone {zone_id}:")
            print(f"    Inlet temp:       {zone_data.get('inlet_temp_c', '?')}°C")
            print(f"    Hot aisle temp:   {zone_data.get('hot_aisle_temp_c', '?')}°C")
            print(f"    CRAC load:        {zone_data.get('crac_load_kw', '?'):.0f} kW")
            cap = zone_data.get('crac_capacity_fraction', 0)
            cap_pct = cap * 100
            flag = " ⚠" if cap > 0.85 else ""
            print(f"    CRAC capacity:    {cap_pct:.0f}%{flag}")

        if self.warnings:
            print("\n  Warnings:")
            for w in self.warnings:
                print(f"    ⚠ {w}")
        print()


class DCModel:
    """
    Data centre floor-level digital twin.

    Usage:
        model = DCModel.from_config("examples/floor2_layout.yaml")

        # set rack loads manually
        model.set_load({"R05-01": 18.0, "R05-02": 15.0, ...})

        # or set a uniform load
        model.set_uniform_load_fraction(0.75)

        result = model.run()
        result.print_summary()
    """

    def __init__(self, layout: FloorLayout):
        self.layout = layout
        self._rack_loads: dict[str, float] = {r.rack_id: r.current_kw for r in layout.racks}

    @classmethod
    def from_config(cls, config_path: str) -> "DCModel":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        zones = [ZoneSpec(**z) for z in cfg.get("zones", [])]
        racks = [RackSpec(**r) for r in cfg.get("racks", [])]

        layout = FloorLayout(
            floor_id=cfg.get("floor_id", "floor_1"),
            zones=zones,
            racks=racks,
            total_ups_capacity_kw=cfg.get("ups_capacity_kw", 5000.0),
        )
        logger.info(f"Loaded floor layout: {layout.floor_id}, {len(zones)} zones, {len(racks)} racks")
        return cls(layout)

    def set_load(self, rack_loads: dict[str, float]):
        """Set specific rack loads (kW). Unspecified racks keep their current load."""
        for rack_id, load_kw in rack_loads.items():
            if rack_id in self._rack_loads:
                self._rack_loads[rack_id] = load_kw
            else:
                logger.warning(f"Unknown rack ID: {rack_id}")

    def set_uniform_load_fraction(self, fraction: float):
        """Set all racks to the same fraction of their max capacity."""
        fraction = max(0.0, min(1.0, fraction))
        for rack in self.layout.racks:
            self._rack_loads[rack.rack_id] = rack.max_kw * fraction

    def _pue_from_crac_loads(self, it_load_kw: float, total_crac_load_kw: float) -> float:
        """
        PUE = Total Facility Load / IT Load
        Total facility load = IT + cooling + lighting/misc overhead
        Lighting/misc typically adds ~2-3% of IT load.
        """
        if it_load_kw <= 0:
            return 1.0
        misc_overhead = it_load_kw * 0.025
        facility_load = it_load_kw + total_crac_load_kw + misc_overhead
        return facility_load / it_load_kw

    def run(self) -> SimResult:
        """Run the steady-state simulation and return results."""
        zone_results = {}
        total_crac_load = 0.0
        total_it_load = sum(self._rack_loads.values())
        warnings = []

        # check UPS headroom
        ups_utilisation = total_it_load / max(self.layout.total_ups_capacity_kw, 1)
        if ups_utilisation > 0.8:
            warnings.append(f"UPS at {ups_utilisation*100:.0f}% capacity — approaching N+1 limit")

        for zone in self.layout.zones:
            zone_rack_ids = [
                r.rack_id for r in self.layout.racks
                if r.row_id in zone.row_ids
            ]
            zone_powers = [self._rack_loads.get(rid, 0.0) for rid in zone_rack_ids]
            zone_it_load = sum(zone_powers)

            if zone_it_load == 0:
                continue

            thermal_config = ZoneThermalConfig(
                zone_id=zone.zone_id,
                n_racks=len(zone_rack_ids),
                crac_supply_temp_c=zone.crac_supply_temp_c,
                crac_airflow_m3_per_s=zone.crac_airflow_m3_per_s,
                n_crac_units=zone.n_crac_units,
                containment=zone.containment,
            )
            thermal_model = ZoneThermalModel(thermal_config)
            zone_thermal = thermal_model.steady_state(zone_powers)
            zone_results[zone.zone_id] = zone_thermal
            total_crac_load += zone_thermal["crac_load_kw"]

            # check ASHRAE limits
            inlet = zone_thermal["inlet_temp_c"]
            cap_frac = zone_thermal["crac_capacity_fraction"]

            if inlet > 27:
                warnings.append(f"Zone {zone.zone_id}: inlet temp {inlet:.1f}°C — approaching ASHRAE A2 limit (35°C)")
            if cap_frac > 0.90:
                warnings.append(f"Zone {zone.zone_id}: CRAC at {cap_frac*100:.0f}% capacity — very limited headroom")
            elif cap_frac > 0.80:
                warnings.append(f"Zone {zone.zone_id}: CRAC at {cap_frac*100:.0f}% capacity — monitor closely")

        pue = self._pue_from_crac_loads(total_it_load, total_crac_load)
        facility_load = total_it_load + total_crac_load + total_it_load * 0.025

        return SimResult(
            floor_id=self.layout.floor_id,
            total_it_load_kw=round(total_it_load, 1),
            total_facility_load_kw=round(facility_load, 1),
            pue=round(pue, 3),
            zone_results=zone_results,
            warnings=warnings,
        )
