"""Bridge to qrart — diffusion-painted QR codes that still scan.

`qrart` (a sibling project, not part of this repo) turns a QR code into AI art
by using the clean black/white code as a ControlNet condition: Stable Diffusion
paints the prompt while the ControlNet forces light and dark to line up with the
modules. Pointed at a sticker's own URL, it replaces the plain module grid with
something worth looking at, and the rest of the disc — the band, the line, the
geometry — is unchanged.

## Why this is a bridge and not an import

qrart depends on torch and diffusers: several gigabytes of machine-learning
stack. The sticker pipeline is six small packages and must stay that way, so
nothing here imports qrart. Generation runs in QRART'S OWN interpreter, as a
subprocess, and the two sides exchange JSON (see _qrart_worker.py). Art is
strictly optional: with qrart absent the build produces exactly the vector
stickers it always did.

Locate qrart with, in order:
    QRART_PYTHON   path to its venv interpreter
    QRART_HOME     the checkout (we use <QRART_HOME>/.venv/bin/python)
    the conventional sibling checkout next to this repo

## Why the payload matters, and why ours is unusually good

qrart's own guidance is that payloads past ~60 characters push the code to a
denser version and tank the success rate — art needs coarse modules to hide in.
A sticker URL is 28 characters, uppercase, and lands in QR's alphanumeric mode:
a 29-module version-3 code, about as sparse as a useful URL gets. The short-code
scheme was chosen for print legibility (codes.py) and turns out to be exactly
what makes this technique viable.

## What this costs

Art is per CODE, and every sticker has its own code — that is the whole point of
the scan flow. So a 12-up sheet is 12 diffusion runs, each generating several
candidates: on Apple Silicon, roughly an hour per sheet at the defaults. Results
are cached by (payload + every parameter that affects the image), so re-running
a build is free and only a changed prompt or a new code pays again.

Nothing here generates on import, and the build never generates unless asked.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Cache lives outside out/, which --clean wipes. Losing an hour of diffusion to
# a routine rebuild would be a bad trade.
CACHE = HERE / "art-cache"

DEFAULT_NEGATIVE = (
    "ugly, disfigured, low quality, blurry, watermark, text, frame, "
    "flat colour, smooth gradient, empty sky"
)


class ArtSpec:
    """Everything that decides what the art looks like.

    Every field is part of the cache key, so changing any of them regenerates
    and changing none of them does not. `candidates` is included even though it
    only affects how many tries we get: a run that settled on candidate 4 is not
    reproducible from a run that only generates 2.
    """

    def __init__(self, prompt, *, negative=DEFAULT_NEGATIVE, ref=None,
                 ref_scale=0.6, qr_strength=1.1, steps=30, cfg=7.0, size=768,
                 candidates=4, seed=None, device=None, fp32=False):
        self.prompt = prompt
        self.negative = negative
        self.ref = str(ref) if ref else None
        self.ref_scale = ref_scale
        self.qr_strength = qr_strength
        self.steps = steps
        self.cfg = cfg
        self.size = size
        self.candidates = candidates
        self.seed = seed
        self.device = device
        self.fp32 = fp32

    def fingerprint(self, url: str) -> str:
        payload = json.dumps({**self.__dict__, "url": url}, sort_keys=True)
        return hashlib.sha1(payload.encode()).hexdigest()[:16]

    def to_spec(self, url: str, out_dir: Path) -> dict:
        return {**self.__dict__, "url": url, "out_dir": str(out_dir)}


def find_python() -> Path | None:
    """qrart's interpreter, or None if we cannot find one."""
    env = os.environ.get("QRART_PYTHON")
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None

    homes = []
    if os.environ.get("QRART_HOME"):
        homes.append(Path(os.environ["QRART_HOME"]).expanduser())
    # The conventional layout: qrart checked out beside the city-edit repo.
    homes.append(HERE.parent.parent.parent / "qrart")

    for home in homes:
        candidate = home / ".venv" / "bin" / "python"
        if candidate.exists():
            return candidate
    return None


def availability() -> tuple[bool, str]:
    """(usable, human-readable reason). Never raises, never imports qrart."""
    python = find_python()
    if python is None:
        return False, (
            "qrart not found. Set QRART_PYTHON to its venv interpreter, or "
            "QRART_HOME to its checkout, or check it out beside this repo."
        )
    probe = subprocess.run(
        [str(python), "-c", "import qrart, torch; "
         "print(qrart.__version__, torch.__version__, "
         "'mps' if torch.backends.mps.is_available() else "
         "('cuda' if torch.cuda.is_available() else 'cpu'))"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return False, f"{python} cannot import qrart: {probe.stderr.strip()[:200]}"
    version, torch_version, device = probe.stdout.split()
    return True, (f"qrart {version} · torch {torch_version} · {device} "
                  f"({python})")


def cached_art(url: str, spec: ArtSpec) -> Path | None:
    """The already-generated art for this payload+spec, if any."""
    path = CACHE / f"{spec.fingerprint(url)}.png"
    return path if path.exists() else None


def generate_art(url: str, spec: ArtSpec, *, verbose: bool = True) -> Path | None:
    """Art for one sticker. Cached; returns None if nothing scannable came out.

    Returning None rather than raising is deliberate: one stubborn code must not
    abort a sheet that is otherwise an hour of finished work. The caller decides
    whether to fall back to the plain vector code or to stop.
    """
    hit = cached_art(url, spec)
    if hit:
        return hit

    python = find_python()
    if python is None:
        raise RuntimeError(availability()[1])

    fp = spec.fingerprint(url)
    work = CACHE / f"work-{fp}"
    proc = subprocess.run(
        [str(python), str(HERE / "_qrart_worker.py"),
         json.dumps(spec.to_spec(url, work))],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        if verbose:
            print(f"    qrart failed: {proc.stderr.strip()[:300]}", file=sys.stderr)
        shutil.rmtree(work, ignore_errors=True)
        return None

    # The worker prints progress to stdout too; the JSON result is the last line.
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        if verbose:
            print(f"    qrart returned no result: {proc.stdout.strip()[-300:]}",
                  file=sys.stderr)
        shutil.rmtree(work, ignore_errors=True)
        return None

    if not result.get("ok"):
        if verbose:
            tried = len(result.get("candidates", []))
            print(f"    no scannable art after {tried} candidate(s): "
                  f"{result.get('error')}", file=sys.stderr)
        shutil.rmtree(work, ignore_errors=True)
        return None

    CACHE.mkdir(parents=True, exist_ok=True)
    final = CACHE / f"{fp}.png"
    shutil.copyfile(result["chosen"], final)
    # Keep the run beside the chosen frame: the rejected candidates and qrart's
    # params.json are what you look at when tuning a prompt.
    (CACHE / f"{fp}.json").write_text(json.dumps(
        {"url": url, "spec": spec.__dict__, "result": result}, indent=2) + "\n")
    return final


def estimate(count: int, spec: ArtSpec) -> str:
    """A rough wall-clock estimate, so nobody starts an overnight run by
    accident. Deliberately coarse — it is a warning, not a promise."""
    per_candidate_s = spec.steps * (spec.size / 768) ** 2 * 2.0  # ~Apple Silicon
    total_h = count * spec.candidates * per_candidate_s / 3600
    return (f"{count} code(s) × {spec.candidates} candidate(s) at {spec.steps} "
            f"steps ≈ {total_h:.1f} h on Apple Silicon (first run also "
            f"downloads ~4 GB of model weights)")
