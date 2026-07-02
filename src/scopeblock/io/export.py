"""Persist and reload a ``Waveform``.

Formats:
* ``csv``  — two columns ``time_s,volts`` plus a commented metadata header.
* ``npz``  — native, lossless round-trip (arrays + meta). Used by ``api.load``.
* ``mf4``  — automotive measurement format, optional (needs ``asammdf``).

File load emits the exact same ``Waveform`` shape as live capture, so nothing
downstream can tell the difference.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..waveform import Waveform


def _export_csv(wf: Waveform, path: Path) -> None:
    header_meta = json.dumps(wf.to_dict())
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(f"# scopeblock waveform; channel={wf.channel}; units={wf.units}\n")
        fh.write(f"# meta={header_meta}\n")
        fh.write("time_s,volts\n")
        for ti, vi in zip(wf.t, wf.v):
            fh.write(f"{ti:.12g},{vi:.12g}\n")


def _export_npz(wf: Waveform, path: Path) -> None:
    np.savez(
        path,
        t=wf.t,
        v=wf.v,
        dt=wf.dt,
        t0=wf.t0,
        channel=wf.channel,
        units=wf.units,
        meta=json.dumps(wf.meta),
    )


def _export_mf4(wf: Waveform, path: Path) -> None:
    try:
        from asammdf import MDF, Signal
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            'MF4 export needs asammdf. Install: pip install -e ".[mf4]"'
        ) from exc
    sig = Signal(samples=wf.v, timestamps=wf.t, name=wf.channel, unit=wf.units)
    mdf = MDF()
    mdf.append([sig])
    mdf.save(path, overwrite=True)


def export(wf: Waveform, path: str | Path, fmt: str | None = None) -> None:
    """Write ``wf`` to ``path``. ``fmt`` defaults to the file extension."""
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    if fmt == "csv":
        _export_csv(wf, path)
    elif fmt == "npz":
        _export_npz(wf, path)
    elif fmt in ("mf4", "mdf"):
        _export_mf4(wf, path)
    else:
        raise ValueError(f"unsupported export format {fmt!r} (use csv, npz, or mf4)")


def load_waveform(path: str | Path) -> Waveform:
    """Reload a ``Waveform`` from ``.npz`` (lossless) or ``.csv``."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".npz":
        with np.load(path, allow_pickle=False) as data:
            return Waveform(
                t=data["t"],
                v=data["v"],
                dt=float(data["dt"]),
                t0=float(data["t0"]),
                channel=str(data["channel"]),
                units=str(data["units"]),
                meta=json.loads(str(data["meta"])),
            )
    if suffix == ".csv":
        meta: dict = {}
        rows = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("# meta="):
                    meta = json.loads(line[len("# meta=") :])
                elif line.startswith("#") or line.startswith("time_s"):
                    continue
                elif line:
                    ti, vi = line.split(",")
                    rows.append((float(ti), float(vi)))
        arr = np.array(rows, dtype=float)
        t, v = arr[:, 0], arr[:, 1]
        dt = float(meta.get("dt", t[1] - t[0] if t.size > 1 else 0.0))
        return Waveform(
            t=t, v=v, dt=dt, t0=float(t[0]) if t.size else 0.0,
            channel=str(meta.get("channel", "CH1")),
            units=str(meta.get("units", "V")),
            meta=meta.get("meta", {}),
        )
    raise ValueError(f"unsupported load format {suffix!r} (use .npz or .csv)")
