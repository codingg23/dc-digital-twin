# dc-digital-twin

A lightweight simulation sandbox for data centre modules. Model the impact of infrastructure changes before touching anything real.

This is a physics-lite digital twin — not CFD, not a full-blown simulation. The goal is to be *useful*, not academically accurate. Operators need to answer questions like: "If I add 8 more GPU racks to Row 5, what happens to the cooling load? Do I have headroom?"

## Overview

The twin models three interconnected systems:

**Power**
- Rack-level load profiles (from forecaster or manual input)
- PDU capacity constraints
- UPS N+1 topology

**Cooling**
- CRAC/CRAH units per zone
- Airflow model (simplified: cold aisle → racks → hot aisle → CRAC return)
- Chiller plant with COP curve vs outdoor ambient

**Thermal**
- Rack inlet/outlet temperatures
- Hot aisle temp as function of row load + airflow rate
- Propagation model for cooling failures

The systems are coupled: load affects thermal, thermal affects cooling demand, cooling affects power (PUE).

## Use Cases

1. **Capacity planning**: "Can I add 200kW of GPU load to floor 2?"
2. **Failure scenario modelling**: "What happens to inlet temps if CRAC-04 fails?"
3. **PUE sensitivity analysis**: "How much does raising the inlet setpoint by 2°C improve PUE?"
4. **Change validation**: Run a proposed rack move through the sim before scheduling it

## Tech Stack

- Python 3.11
- NumPy for the core simulation
- `networkx` for facility topology graph
- Plotly for interactive 3D layout visualisation
- Gradio for the demo UI (fastest way to make it usable without building a full frontend)

## How to Run

```bash
git clone https://github.com/aryan-veltora/dc-digital-twin
cd dc-digital-twin
pip install -r requirements.txt

# run the interactive demo
python app.py

# or run a scenario programmatically
python -c "
from simulation.dc_model import DCModel, SimConfig
from simulation.scenarios import add_gpu_load_scenario

model = DCModel.from_config('examples/floor2_layout.yaml')
result = add_gpu_load_scenario(model, additional_kw=200, row='ROW-05')
result.print_summary()
"
```

## Example Output

```
Scenario: +200kW GPU load to ROW-05
─────────────────────────────────────────────────────
Current state:
  Floor 2 IT load:     1,240 kW
  CRAC capacity used:  68%
  Avg inlet temp:      21.3°C
  PUE:                 1.44

After change:
  Floor 2 IT load:     1,440 kW  (+16%)
  CRAC capacity used:  83%       ← approaching limit
  Avg inlet temp:      22.8°C    (+1.5°C)
  Hottest rack:        R05-14 @ 27.1°C  ⚠ within 8°C of ASHRAE A2 limit
  PUE:                 1.47

Recommendation:
  You have headroom, but CRAC-07 will be at 94% capacity on warm days.
  Consider adding a supplemental cooling unit or moving some load to Floor 3.
  Thermal risk: LOW-MEDIUM
─────────────────────────────────────────────────────
```

## Results / Learnings

The interesting finding: PUE sensitivity to inlet setpoint is non-linear. Going from 21°C to 23°C setpoint gives you a bigger efficiency gain than 23°C to 25°C, because the chiller COP curve drops off at higher loads. This isn't obvious without the sim.

The model is deliberately conservative — it tends to flag risks a bit earlier than reality. I'd rather have operators check something that turns out to be fine than miss something that cascades.

Main limitation: the airflow model is a massive simplification. Real hot aisle/cold aisle dynamics are 3D and depend heavily on tile placement, blanking panels, cable management. Getting that right would require actual CFD (or real sensor calibration data from the specific facility).

## Folder Structure

```
dc-digital-twin/
├── simulation/
│   ├── dc_model.py        # main model orchestrator
│   ├── thermal.py         # thermal physics model
│   ├── power_flow.py      # power topology + constraints
│   ├── cooling.py         # CRAC/chiller models
│   └── scenarios.py       # pre-built change scenarios
├── visualization/
│   ├── floor_map.py       # 2D floor plan with heat overlay
│   └── timeline.py        # time-series result plots
├── examples/
│   └── floor2_layout.yaml
├── tests/
└── app.py                 # Gradio demo UI
```

## TODO

- [ ] Validate model against real facility data (have a lead, waiting on NDA)
- [ ] Liquid cooling support (rear-door HX, immersion)
- [ ] Multi-floor propagation (shared chiller plant)
- [ ] Export scenario results for operator review/sign-off
