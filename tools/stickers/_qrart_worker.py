"""Generate one sticker's QR art. Runs inside QRART's virtualenv, not ours.

This file is never imported by the sticker build. It is executed by
qrart_bridge.py with qrart's own interpreter, because qrart pulls in torch and
diffusers — several gigabytes of machine-learning stack that the sticker
pipeline has no business depending on. The two dependency trees stay completely
separate and talk over a JSON pipe.

    <qrart-python> _qrart_worker.py '<json spec>'   → JSON on stdout

The spec names the payload, the prompt and the diffusion knobs; the result names
the chosen image, or explains why nothing was chosen.

## The gate

qrart's own check asks "does this decode?". That is not the question here. The
question is "does this decode to the URL printed on THIS sticker?" — because a
diffusion model painting over a QR can, in principle, produce something that
decodes to a different string, and a sticker that scans beautifully to the wrong
place is worse than one that does not scan at all. So a candidate is accepted
only on an exact match with the payload we asked for.

Candidates are generated as a batch and the first exact match wins. Seed
variance in this technique is large — a prompt that fails at one seed often
lands at the next — which is why a run is several candidates rather than one
retry loop.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    spec = json.loads(sys.argv[1])
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from qrart import qr as qrmod
        from qrart import generate as gen
        from qrart import verify
    except ImportError as e:
        print(json.dumps({"ok": False, "error": f"qrart not importable: {e}"}))
        return 1

    url = spec["url"]
    # Build the control QR from OUR payload with qrart's own encoder, so the
    # module grid the diffusion model is conditioned on is exactly the grid we
    # would otherwise have drawn as vectors.
    control = qrmod.make_qr(url, spec["size"])

    cfg = gen.GenConfig(
        prompt=spec["prompt"],
        negative=spec["negative"],
        ref_path=Path(spec["ref"]) if spec.get("ref") else None,
        ref_scale=spec["ref_scale"],
        qr_strength=spec["qr_strength"],
        steps=spec["steps"],
        cfg=spec["cfg"],
        size=spec["size"],
        num=spec["candidates"],
        seed=spec.get("seed"),
        device=spec.get("device"),
        fp32=spec.get("fp32", False),
    )

    paths, base_seed = gen.generate(cfg, control, out_dir)

    # Accept only an EXACT match with the payload we asked for — see the module
    # docstring on why "it decodes" is the wrong bar.
    results = []
    chosen = None
    for p in paths:
        decoded = verify.try_decode(p)
        results.append({"file": p.name, "decoded": decoded, "match": decoded == url})
        if chosen is None and decoded == url:
            chosen = p

    print(json.dumps({
        "ok": chosen is not None,
        "chosen": str(chosen) if chosen else None,
        "base_seed": base_seed,
        "candidates": results,
        "error": None if chosen else
                 "no candidate decoded to the sticker's own URL",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
