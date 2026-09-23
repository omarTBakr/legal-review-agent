"""
Loading the models in 4-bit or 8-bit.

Both models are 0.6B, so in bfloat16 they need about 3 GB of VRAM together and
fit on a modest card as they are. Quantising buys headroom — for a larger
model beside them, or a card doing other work — rather than speed, and costs a
little accuracy, which matters more for recognition than for synthesis.

Nothing here is required: `VOICE_QUANTIZATION=none` loads the weights as they
come, and a model that will not quantise falls back to that rather than
failing to load at all.
"""

from logger import get_logger

logger = get_logger(__name__)

MODES = ("none", "8bit", "4bit")


def config_for(mode: str, compute_dtype: str):
    """
    A BitsAndBytesConfig for `mode`, or None when the weights load as they are.

    NF4 with double quantisation, which is the usual choice for transformer
    weights, and bfloat16 for the compute itself so quality does not drop
    further than the storage format already costs.
    """
    mode = (mode or "none").strip().lower()

    if mode in ("", "none", "off", "false"):
        return None

    if mode not in MODES:
        logger.warning("unknown VOICE_QUANTIZATION %r; loading the weights unquantised", mode)
        return None

    import torch
    from transformers import BitsAndBytesConfig

    dtype = getattr(torch, compute_dtype, torch.bfloat16)

    if mode == "8bit":
        return BitsAndBytesConfig(load_in_8bit=True)

    return BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=dtype,
    )


def load_with(loader, model_id: str, mode: str, compute_dtype: str, **kwargs):
    """
    Loads a model, quantised if asked for, unquantised if that does not work.

    Not every model accepts a quantisation config — a composite one with a
    codec alongside the language model may not — and a voice service that
    refuses to start is worse than one using a little more VRAM.
    """
    quantization = config_for(mode, compute_dtype)

    if quantization is not None:
        try:
            model = loader(model_id, quantization_config=quantization, **kwargs)
            logger.info("loaded %s in %s", model_id, mode)
            return model
        except Exception as exc:
            logger.warning("could not load %s in %s (%s); loading it unquantised", model_id, mode, exc)

    return loader(model_id, **kwargs)
