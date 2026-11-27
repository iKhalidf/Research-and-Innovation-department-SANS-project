# Forecast Replay

Map 2 compares a Chronos-2 forecast of the next hour's departures on 34C and 34L with a baseline that repeats the previous hour's count. Open **Flight Mode → Forecast Replay**, reveal the test count for each hour, and view the two scores after the twelfth test. The public example uses invented counts. The local server prefers an exported A2 replay when available.

## Run with local A2 data

Use an available A2 `flights.json` and its sibling `preflight.json`. The forecast uses candidates with runway status `accepted_departure_candidate` on 34C or 34L and groups them by the UTC hour of `anchor_ms`. The inventory must list all 24 nonempty provider files for that date. This checks file coverage. Local ADS-B detections may differ from airport movement totals.

In the project Python environment, install the optional forecast dependency and run the generator from the repository root:

```text
python -m pip install -e ".[forecast]"
python -m sans_forecast --flights ../SANS-output/a2/2026-10-01/flights.json --date 2026-10-01 --output ../SANS-output/a2/2026-10-01/forecast-replay.json
python -m sans_a2 serve
```

Use `--preflight` for an inventory stored elsewhere. If an independent hourly export exists, use `--counts path/to/hourly.csv --source a2` instead of `--flights`. The CSV must have columns `hour_utc,count` and one row for every UTC hour of `--date`, formatted like `2026-10-01T00:00:00Z,4`. Enter zero only for confirmed zero counts.

The A2 server reads exactly `forecast-replay.json` from the configured A2 output directory. Real replay files stay in that local directory. The standalone HTML bundle covers the illustrative demo. Use the served Map 2 page for Forecast Replay.

## Rebuild the public example

```text
python -m sans_forecast --counts data/Forecast_Replay_Synthetic.csv --source synthetic --date 2026-10-02 --output map-2/forecast-example.json
```

The command records the source hash and inference metadata. The GitHub Pages build includes the example only if it is labeled synthetic. Map 2 displays a synthetic example banner and uses its scores to demonstrate the interface.

## Read the results

- **Forecast:** [Amazon Chronos-2 weights](https://huggingface.co/amazon/chronos-2/tree/29ec3766d36d6f73f0696f85560a422f50e8498c), revision `29ec3766d36d6f73f0696f85560a422f50e8498c`, via [chronos-forecasting 2.3.2](https://pypi.org/project/chronos-forecasting/2.3.2/) from the [upstream code repository](https://github.com/amazon-science/chronos-forecasting/releases/tag/v2.3.2). The generator uses the median output, rounds to the nearest integer with ties up, and floors negative predictions at zero.
- **Replay:** the first 12 UTC bins supply context. For each of the next 12 hours, both methods use only prior bins. The percentage is the number of predictions within ±2 departures divided by 12. Mean absolute error is in departures per hour. A2 outcomes come from finalized retrospective detections, which can depend on later reports. This test covers one day and a one-hour horizon.
- **Impacts:** apply the forecast count to one explicitly selected runway, SID and aircraft type. The existing OpenAP and Doc 29 scenario provides the fuel and CO₂ totals through 10,000 ft and the per-departure LAmax contours. Residents inside the footprint are counted once. Runway, route and aircraft are scenario inputs, and the contour is a single-event footprint.
