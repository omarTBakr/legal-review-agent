"""
Two changes the `kokoro` package needs before it can load this distilled model.

Upstream Kokoro hardcodes the decoder's hidden width at 1024 and its output at
512, which is right for the 82M model and wrong for a distilled one. The 7M
distill's config sets `hidden_channels` and `out_channels`, and the stock
package raises `TypeError: Decoder.__init__() got an unexpected keyword
argument 'hidden_channels'` on it.

The model's own repository ships a patched copy of the whole package to get
around this. We apply the same change here instead — about fifteen lines,
derived from upstream's `Decoder.__init__` (kokoro, Apache 2.0) with the two
constants made into parameters — because a service that downloads and imports
Python from a model repository at startup is a supply chain nobody reviewed.

**The decoder's width.** Upstream hardcodes 1024 and 512; this config sets
`hidden_channels` and `out_channels`, so those become parameters.

**How weight normalisation is stored.** The checkpoint was saved with
`torch.nn.utils.parametrizations.weight_norm`, which keeps
`parametrizations.weight.original0/1`, while upstream still builds its layers
with the deprecated `torch.nn.utils.weight_norm` and its `weight_g`/`weight_v`.
The names do not meet, the weights are quietly skipped, and the model produces
confident noise — which is exactly what happened here before this was fixed,
and why the round trip below is part of the deal rather than a nicety.

The vendored package's remaining differences — German support, Apple Silicon
device selection — have nothing to do with this model, which is the other
reason not to swap the whole package in.

Correctness is not taken on trust either: rebuilding these layers wrongly
produces noise rather than an error, so `voice/tests/test_kokoro_patch.py`
checks the shapes, and the service's own round trip — synthesise a sentence,
transcribe it back with the ASR model — catches anything that sounds wrong.
"""

from logger import get_logger

logger = get_logger(__name__)

# what upstream builds, and what a config is allowed to override
DEFAULT_HIDDEN = 1024
DEFAULT_OUT = 512

_applied = False


def apply() -> None:
    """Makes the installed kokoro able to load this checkpoint. Idempotent."""
    global _applied

    if _applied:
        return

    import torch.nn as nn
    from kokoro import istftnet, modules
    from kokoro.istftnet import AdainResBlk1d, Decoder, Generator
    from torch.nn.utils.parametrizations import weight_norm

    # the layers have to be built the way the checkpoint was saved, or their
    # weights simply do not load and what comes out is noise
    istftnet.weight_norm = weight_norm
    modules.weight_norm = weight_norm

    class ConfigurableDecoder(Decoder):
        """kokoro's Decoder, with `hidden_channels` and `out_channels` honoured."""

        def __init__(
            self,
            dim_in,
            style_dim,
            dim_out,
            resblock_kernel_sizes,
            upsample_rates,
            upsample_initial_channel,
            resblock_dilation_sizes,
            upsample_kernel_sizes,
            gen_istft_n_fft,
            gen_istft_hop_size,
            disable_complex=False,
            hidden_channels=DEFAULT_HIDDEN,
            out_channels=DEFAULT_OUT,
        ):
            # nn.Module's constructor, not Decoder's: the point is to build
            # these layers at a different width, so the parent's would only
            # build them twice, at the wrong one
            nn.Module.__init__(self)

            self.encode = AdainResBlk1d(dim_in + 2, hidden_channels, style_dim)

            self.decode = nn.ModuleList()
            # three blocks at the hidden width, then one that narrows and
            # upsamples — as upstream, whose widths were 1024 and 512
            for _ in range(3):
                self.decode.append(AdainResBlk1d(hidden_channels + 2 + 64, hidden_channels, style_dim))
            self.decode.append(AdainResBlk1d(hidden_channels + 2 + 64, out_channels, style_dim, upsample=True))

            self.F0_conv = weight_norm(nn.Conv1d(1, 1, kernel_size=3, stride=2, groups=1, padding=1))
            self.N_conv = weight_norm(nn.Conv1d(1, 1, kernel_size=3, stride=2, groups=1, padding=1))
            # `asr`, the text encoder's output, is what goes through here: its
            # width is dim_in, not the decode blocks'. Upstream writes 512
            # because in the 82M model dim_in and out_channels are both 512,
            # which hides the difference until they are shrunk apart
            self.asr_res = nn.Sequential(weight_norm(nn.Conv1d(dim_in, 64, kernel_size=1)))

            self.generator = Generator(
                style_dim,
                resblock_kernel_sizes,
                upsample_rates,
                upsample_initial_channel,
                resblock_dilation_sizes,
                upsample_kernel_sizes,
                gen_istft_n_fft,
                gen_istft_hop_size,
                disable_complex=disable_complex,
            )

    istftnet.Decoder = ConfigurableDecoder

    # KModel imports the name directly, so the module attribute alone is not
    # enough — whichever module already holds a reference has to be updated too
    from kokoro import model as kokoro_model

    if hasattr(kokoro_model, "Decoder"):
        kokoro_model.Decoder = ConfigurableDecoder

    _applied = True
    logger.info("patched kokoro: configurable decoder width, parametrized weight norm")
