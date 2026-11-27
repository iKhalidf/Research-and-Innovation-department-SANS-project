"""Load the pinned checkpoint only when running forecast inference."""

from importlib.metadata import PackageNotFoundError, version

from .replay import MODEL_ID, MODEL_REVISION, QUANTILE, RUNTIME, RUNTIME_VERSION


def chronos_predictor(device: str):
    try:
        installed = version(RUNTIME)
    except PackageNotFoundError:
        installed = None
    if installed != RUNTIME_VERSION:
        raise ValueError(f"Install {RUNTIME}=={RUNTIME_VERSION} in the forecast environment.")

    import torch
    from chronos import Chronos2Pipeline

    pipeline = Chronos2Pipeline.from_pretrained(MODEL_ID, revision=MODEL_REVISION, device_map=device)

    def predict(history):
        context = torch.tensor(history, dtype=torch.float32)
        quantiles, _ = pipeline.predict_quantiles(
            [context], prediction_length=1, quantile_levels=[QUANTILE]
        )
        return quantiles[0][0, 0, 0].item()

    return predict
