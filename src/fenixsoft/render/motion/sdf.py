"""Signed-distance primitives used by the motion plate renderer.

The brain preset is extracted from the motion-graphics lab (`render_v6_learned.py`)
and kept parameter-free so the same shape can be reused with any camera/light.
"""
from __future__ import annotations

import numpy as np


def smin(a: np.ndarray, b: np.ndarray, k: float = 0.1) -> np.ndarray:
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b * (1.0 - h) + a * h - k * h * (1.0 - h)


def sd_ellipsoid(px, py, pz, cx, cy, cz, rx, ry, rz):
    qx = (px - cx) / rx
    qy = (py - cy) / ry
    qz = (pz - cz) / rz
    k0 = np.sqrt(qx * qx + qy * qy + qz * qz + 1e-8)
    k1 = np.sqrt((qx / rx) ** 2 + (qy / ry) ** 2 + (qz / rz) ** 2 + 1e-8)
    return k0 * (k0 - 1.0) / k1


def sd_sphere(px, py, pz, r=0.8):
    return np.sqrt(px * px + py * py + pz * pz) - r


def sd_torus(px, py, pz, r_major=0.62, r_minor=0.24):
    q = np.sqrt(px * px + pz * pz) - r_major
    return np.sqrt(q * q + py * py) - r_minor


def sd_blob(px, py, pz):
    """Organic ellipsoid with a low-frequency surface ripple."""
    d = sd_ellipsoid(px, py, pz, 0, 0, 0, 0.72, 0.82, 0.68)
    ripple = (np.sin(px * 6.0 + np.cos(py * 5.0))
              * np.cos(py * 7.0 - np.sin(pz * 4.5)))
    return d - 0.03 * ripple


def brain_sdf(px, py, pz):
    """Learned proportions: 1.00 length : 0.90 width : 0.82 height."""
    taper = 1.0 - 0.14 * np.clip(py, 0.0, 1.0)
    px_t = px / taper

    d_left = sd_ellipsoid(px_t, py, pz, -0.28, 0.02, 0.10, 0.45, 0.86, 0.62)
    d_right = sd_ellipsoid(px_t, py, pz, 0.28, 0.02, 0.10, 0.45, 0.86, 0.62)
    d = smin(d_left, d_right, k=0.045)

    d_tl = sd_ellipsoid(px, py, pz, -0.44, 0.0, -0.22, 0.27, 0.44, 0.25)
    d_tr = sd_ellipsoid(px, py, pz, 0.44, 0.0, -0.22, 0.27, 0.44, 0.25)
    d = smin(d, d_tl, k=0.12)
    d = smin(d, d_tr, k=0.12)

    d_cereb = sd_ellipsoid(px, py, pz, 0.0, -0.42, -0.34, 0.40, 0.27, 0.22)
    d = smin(d, d_cereb, k=0.08)

    gyri = np.sin(px * 12.0 + np.cos(py * 9.5)) * np.cos(py * 13.0 - np.sin(pz * 8.5))
    return d - 0.014 * np.abs(gyri)


PRESETS = {
    "brain": brain_sdf,
    "sphere": sd_sphere,
    "torus": sd_torus,
    "blob": sd_blob,
}
