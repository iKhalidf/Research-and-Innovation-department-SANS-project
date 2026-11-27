# Ask Masar

Ask Masar is available in the local Map 2 **Scenario (What-if)** panel. The assistant interprets a question, shows the inputs it found, and runs the simulations when you choose **Run calculations**. The model does not supply the reported numbers.

## Start it locally

Install the pinned Python packages from the project root.

```text
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
```

For free-text questions, create an API key in the [Groq Console](https://console.groq.com/keys). In PowerShell 7, enter it at a masked prompt before starting the viewer.

```powershell
$env:GROQ_API_KEY = Read-Host 'Groq API key' -MaskInput
python -m sans_a2 serve
```

If you already saved `GROQ_API_KEY` as a Windows user environment variable, open a new terminal or import it into the current one with `$env:GROQ_API_KEY = [Environment]::GetEnvironmentVariable('GROQ_API_KEY', 'User')`. The server defaults to `openai/gpt-oss-20b`. Set `MASAR_GROQ_MODEL=openai/gpt-oss-120b` in the server environment to try the larger model. The Python server sends the question, scenario selections, and any follow-up to Groq. The key stays in the server environment and calculations run locally. [Groq structured outputs](https://console.groq.com/docs/structured-outputs)

For offline free-text interpretation, run `ollama serve`, pull `qwen3.5:4b`, and launch the viewer with `MASAR_LLM_PROVIDER=ollama` in its environment. Set `MASAR_OLLAMA_MODEL` to select another compatible local model. Guided prompts and the Scenario panel run without a hosted API key. The public Pages build keeps the manual panel.

Open [local Map 2](http://127.0.0.1:8765/map-2/), then choose **Flight Mode → Scenario (What-if)**. **Ask Masar** appears at the top of the panel. Choose **Estimate fuel**, **Compare takeoff weights**, or **Compare noise by temperature**, edit the values, then select **Review inputs**. Guided prompts use the runway, SID, and aircraft selected in Scenario mode. The local calculator validates the exact inputs before showing the review. Guided prompts work without Ollama.

You can also type a question. Try:

- “Compare 20 A320 departures on ISLAM 2W at 75% versus 80% of maximum takeoff weight.”
- “For the current route and aircraft, compare the noise footprint at 30 °C and 38 °C.”
- “What does LAmax mean?”

Questions can resolve to one case or two. Omitted route and aircraft fields use the panel selection where it fits. An omitted count means one departure. If the assistant asks a follow-up, answer it below the original question to keep the rest of the plan.

The sidebar names the model while it interprets a question and shows elapsed time. After you select **Run calculations**, it shows the current step and case number. Open **Calculation steps** to see the steps the worker reached, including fast steps such as the CO₂ conversion. Every run estimates fuel, CO₂, and noise. A noise run can take tens of seconds per case. Choose **Show case on map** for its contour and **Restore manual map** to return to the selected catalog case.

## Compare interpreters

With `GROQ_API_KEY` set, benchmark either or both hosted models using the same 40 questions. The command spaces requests for the free tier.

```text
python tools/scenarios/benchmark_ask_masar.py --models openai/gpt-oss-20b openai/gpt-oss-120b --output ask-masar-groq-benchmark.json
```

For the 40-question benchmark, install all three Ollama builds and run the benchmark when the preview is idle:

```text
ollama pull qwen3.5:4b
ollama pull qwen3.5:2b-q4_K_M
ollama pull qwen2.5:1.5b
python tools/scenarios/benchmark_ask_masar.py --output ask-masar-benchmark.json
```

The report records exact input matches, clarification checks, the first request, and median and 95th-percentile times. It records a cold start for Ollama, where Ask Masar keeps the selected model loaded for 10 minutes after a question.

## Read the results

- **Fuel:** [OpenAP 2.6.2](https://github.com/TUDelft-CNS-ATM/openap) WRAP and FuelFlow integrate from brake release to 10,000 ft on the standard climb. Mass affects the fuel calculation. Plan totals multiply unrounded per-departure fuel by the number of departures. CO₂ is fuel × 3.16.
- **Noise:** the [project’s Doc 29 implementation](https://github.com/M40aha/oejn-noise-population) uses the existing EASA ANP 2.3 data and WorldPop receptors. Temperature affects noise under still air, 1010 hPa, and the baseline relative humidity. The 60, 65 and 70 dB LAmax contours and estimated resident counts are for one departure.
- The job uses the project's existing inputs, writes intermediates to a temporary working directory, and displays model versions, input hashes, and model flags. The values are simulation estimates under the shown assumptions.
