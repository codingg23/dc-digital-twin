# dc-digital-twin

A lightweight simulation sandbox for data centre modules. Model the impact of infrastructure changes before touching anything real.

This is a physics lite digital twin - not CFD, not a full blown simulation. The goal is to be useful, not academically accurate. Operators need to answer questions like "if I add 8 more GPU racks to Row 5, what happens to the cooling load?" before they've committed to anything.

## Overview

The twin models three interconnected systems:

**Power**
- Rack level load profiles (from the forecaster or manual input)
- PDU capacity constraints
- UPS N+1 topology

**Cooling**
- CRAC/CRAH units per zone
- Simplified airflow model (cold aisle to racks to hot aisle to CRAC return)
- Chiller plant with COP curve vs outdoor ambient

**Thermal**
- Rack inlet/outlet temperatures
- Hot aisle temp as a function of row load and airflow rate
- Propagation model for cooling failures

The three systems are coupled: load affects thermal, thermal affects cooling demand, cooling affects power (PUE).

## Use Cases

1. **Capacity planning** - can I add 200kW of GPU load to floor 2?
2. **Failure scenario modelling** - what happens to inlet temps if CRAC-04 goes down?
3. **PUE sensitivity analysis** - how much does raising the inlet setpoint by 2 degrees improve PUE?
4. **Change validation** - run a proposed rack move through the sim before scheduling it

## Tech Stack

- Python 3.11
- NumPy for the core simulation
- `networkx` for facility topology graph
- Plotly for interactive layout visualisation
- Gradio for the demo UI

## How to Run

```bash
git clone https://github.com/codingg23/dc-digital-twin
cd dc-digital-twin
pip install -r requirements.txt

# run the interactive demo
python app.py

# or run a scenario from Python
python -c "
from simulation.dc_model import DCModel
from simulation.scenarios import add_load_scenario

model = DCModel.from_config('examples/floor2_layout.yaml')
result = add_load_scenario(model, additional_kw=200, row='ROW-05')
result.print_summary()
"
```

## Example Output

```
Scenario: +200kW load to ROW-05
-------------------------------------------------------
                               Before       After
IT load (kW)                    1,240       1,440
PUE                             1.440       1.470

Zone ZONE-02:
  Inlet temp:       21.3°C     22.8°C
  Hot aisle temp:   31.2°C     33.8°C
  CRAC capacity:    68%        83%

Risk: MEDIUM
Recommendation: Proceed with caution. Monitor cooling metrics closely
for 24h after change.
-------------------------------------------------------
```

## Results / Learnings

Interesting finding: PUE sensitivity to inlet setpoint is non linear. Going from 21 to 23 degrees gives a bigger efficiency gain than 23 to 25, because the chiller COP curve drops off at higher loads. This isn't obvious without modelling it.

The model is deliberately conservative - it tends to flag risks a bit earlier than reality. I'd rather operators check something that turns out to be fine than miss something that cascades into a thermal incident.

Main limitation: the airflow model is a big simplification. Real hot aisle/cold aisle dynamics are 3D and depend on tile placement, blanking panels, cable management. Getting that right would need actual CFD or calibration data from the specific facility.

## Folder Structure

```
dc-digital-twin/
+-- simulation/
|   +-- dc_model.py       main model orchestrator
|   +-- thermal.py        thermal physics model
|   +-- cooling.py        CRAC/chiller models
|   +-- scenarios.py      pre-built change scenarios
+-- examples/
|   +-- floor2_layout.yaml
+-- app.py                Gradio demo UI
```

## TODO

- [ ] Validate model against real facility data (have a lead, waiting on NDA)
- [ ] Liquid cooling support (rear door HX, immersion)
- [ ] Multi floor propagation (shared chiller plant)
- [ ] Export scenario results for operator review
