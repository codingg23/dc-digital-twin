"""
scenarios.py

Pre-built change scenarios for the digital twin.

These are the kinds of questions operators actually ask:
  - "Can I add more load to this row?"
  - "What happens if CRAC-04 goes down?"
  - "What's the PUE impact of raising inlet setpoint by 2°C?"

Each function takes a DCModel, modifies it, runs the sim,
and returns a before/after comparison.
"""
from dataclasses import dataclass
from typing import Optional
from .dc_model import DCModel, SimResult


@dataclass
class ScenarioResult:
    scenario_name: str
    baseline: SimResult
    modified: SimResult
    delta_pue: float
    delta_it_load_kw: float
    max_inlet_delta_c: float
    recommendation: str
    risk_level: str  # "low", "medium", "high", "critical"

    def print_summary(self):
        print(f"\nScenario: {self.scenario_name}")
        print("─" * 55)
        print(f"{'':30s} {'Before':>10} {'After':>10}")
        print(f"{'IT load (kW)':30s} {self.baseline.total_it_load_kw:>10.0f} {self.modified.total_it_load_kw:>10.0f}")
        print(f"{'PUE':30s} {self.baseline.pue:>10.3f} {self.modified.pue:>10.3f}")
        print(f"\nRisk: {self.risk_level.upper()}")
        print(f"Recommendation: {self.recommendation}")
        if self.modified.warnings:
            print("\nWarnings:")
            for w in self.modified.warnings:
                print(f"  ⚠ {w}")
        print()


def add_load_scenario(model: DCModel, additional_kw: float, row: Optional[str] = None) -> ScenarioResult:
    """
    Simulate adding a block of new IT load.
    If row is specified, concentrate it there. Otherwise distribute evenly.
    """
    import copy
    baseline_result = model.run()

    modified_model = copy.deepcopy(model)

    if row:
        row_racks = [r for r in modified_model.layout.racks if r.row_id == row]
        if not row_racks:
            raise ValueError(f"Row {row} not found in layout")
        per_rack = additional_kw / len(row_racks)
        for rack in row_racks:
            current = modified_model._rack_loads.get(rack.rack_id, 0)
            modified_model._rack_loads[rack.rack_id] = min(current + per_rack, rack.max_kw)
    else:
        all_racks = modified_model.layout.racks
        per_rack = additional_kw / len(all_racks)
        for rack in all_racks:
            current = modified_model._rack_loads.get(rack.rack_id, 0)
            modified_model._rack_loads[rack.rack_id] = min(current + per_rack, rack.max_kw)

    modified_result = modified_model.run()

    delta_pue = modified_result.pue - baseline_result.pue

    # find max inlet temp change across zones
    max_inlet_delta = 0.0
    for zone_id in baseline_result.zone_results:
        b_inlet = baseline_result.zone_results[zone_id].get("inlet_temp_c", 0)
        m_inlet = modified_result.zone_results.get(zone_id, {}).get("inlet_temp_c", 0)
        max_inlet_delta = max(max_inlet_delta, m_inlet - b_inlet)

    # assess risk
    risk = "low"
    for zone_data in modified_result.zone_results.values():
        cap = zone_data.get("crac_capacity_fraction", 0)
        inlet = zone_data.get("inlet_temp_c", 0)
        if cap > 0.95 or inlet > 30:
            risk = "critical"
        elif cap > 0.85 or inlet > 27:
            risk = "high" if risk != "critical" else risk
        elif cap > 0.75:
            risk = "medium" if risk == "low" else risk

    if risk == "low":
        rec = "Change looks safe. Proceed with normal change management process."
    elif risk == "medium":
        rec = "Proceed with caution. Monitor cooling metrics closely for 24h after change."
    elif risk == "high":
        rec = "Consider adding supplemental cooling or moving some load to another zone before proceeding."
    else:
        rec = "Do not proceed. Insufficient cooling headroom  -  risk of thermal exceedance."

    return ScenarioResult(
        scenario_name=f"+{additional_kw:.0f}kW load" + (f" to {row}" if row else " distributed"),
        baseline=baseline_result,
        modified=modified_result,
        delta_pue=round(delta_pue, 4),
        delta_it_load_kw=additional_kw,
        max_inlet_delta_c=round(max_inlet_delta, 2),
        recommendation=rec,
        risk_level=risk,
    )


def crac_failure_scenario(model: DCModel, crac_id: str, failure_mode: str = "full") -> ScenarioResult:
    """
    Simulate a CRAC unit failure and assess thermal impact.
    failure_mode: 'full' = unit offline, 'partial' = 50% capacity
    """
    import copy
    baseline_result = model.run()
    modified_model = copy.deepcopy(model)

    # Find zone containing this CRAC and reduce its capacity
    for zone in modified_model.layout.zones:
        if crac_id in [f"CRAC-{zone.zone_id}-{i}" for i in range(zone.n_crac_units)]:
            if failure_mode == "full":
                zone.n_crac_units = max(0, zone.n_crac_units - 1)
            elif failure_mode == "partial":
                zone.crac_airflow_m3_per_s *= 0.5

    modified_result = modified_model.run()
    delta_pue = modified_result.pue - baseline_result.pue

    max_inlet_delta = max(
        (modified_result.zone_results.get(z, {}).get("inlet_temp_c", 0) -
         baseline_result.zone_results.get(z, {}).get("inlet_temp_c", 0))
        for z in baseline_result.zone_results
    ) if baseline_result.zone_results else 0.0

    risk = "high" if modified_result.warnings else "medium"
    rec = "Dispatch facilities team immediately. Pre-cool affected racks by throttling compute if inlet approaches 28°C."

    return ScenarioResult(
        scenario_name=f"CRAC failure: {crac_id} ({failure_mode})",
        baseline=baseline_result,
        modified=modified_result,
        delta_pue=round(delta_pue, 4),
        delta_it_load_kw=0.0,
        max_inlet_delta_c=round(max_inlet_delta, 2),
        recommendation=rec,
        risk_level=risk,
    )
