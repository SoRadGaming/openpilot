"""FORK(LEAD-BRAKE): brake sooner when the car ahead brakes hard (lead_brake_assist.py).

TestProjection pins the projection of leadOne: what counts as a radar sample, the raw decel and every gate on it (a
moving track, range, falling sample after sample, radard's own aLeadK, the two windows, the camera or a second sample),
held / vision / stationary / oncoming leads left out, a track change or a dropout restarting it, the tau attack
confirmed on two distinct samples, never prolonging an acceleration nor a braking that ended, and three logged radar
speed glitches (routes fd, c8, 12a) that the first version braked for. TestPlanner drives the real
LongitudinalPlanner (MPC included) behind a lead that brakes hard: mode 1 (shadow) is bit for bit the planner without
the assist and logs what mode 2 would do, mode 2 plans the braking sooner and its projection ends a re-entry ramp, the
comparison solve reproduces the planner's own solve exactly and is skipped when another layer changed the MPC's
candidate, radarState's own time is used, every episode reaches a log line, every other car / stock ACC / mode 0
builds nothing, and a failure turns the assist off without touching the plan.
"""
import json
import math
import os
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import numpy as np

from opendbc.car import structs
from opendbc.car.honda.values import CAR
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.cereal import custom, log, messaging
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.radard import KalmanParams, Track
from openpilot.sunnypilot.selfdrive.controls.lib import lead_brake_assist as lba
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import held_id

V0 = 20.0
OLD: dict[str, Any] = {'raw_d_max': 80.0, 'raw_cam_a': None, 'raw_one_step': 0.0, 'tau_release': False}   # the first version (62e04f92d), for the regressions


def cam_lead(x, a, prob=1.0):
  """modelV2 leadsV3[0] as the assist reads it: the camera's lead at x m with accel a."""
  return SimpleNamespace(prob=prob, x=[x] * 6, a=[a] * 6)


def lead(tid=7, d=35.0, v_lead=V0, a=0.0, tau=1.5, radar=True, present=True, v_ego=V0):
  return SimpleNamespace(present=present, radar=radar, radarTrackId=tid, dRel=d, vRel=v_lead - v_ego, vLead=v_lead,
                         vLeadK=v_lead, aLeadK=a, aLeadTau=tau, modelProb=1.0, yRel=0.0)


def braking(n=80, brake_at=20, a_brake=-4.0, d0=35.0, v0=V0, tid=7, noise=None, cam_a=-1.0, cam_lag=4, brake_n=None):
  """radarState leadOne frames (20 Hz) behind a lead at v0 that brakes at a_brake from frame brake_at (for brake_n
  frames, else to a stop): the radar reports at 10 Hz (each reading twice), quantized as the Elesys decode (1/128 m,
  1/64 m/s), and radard's own Track (Kalman aLeadK, aLeadTau) runs on it. The car holds v0 (open loop). Each frame
  carries the camera's lead (.camera): at the radar's range, accel 0 until cam_lag frames into the braking and cam_a
  from there (cam_a None: no camera lead)."""
  kp = KalmanParams(DT_MDL)
  tr = None
  v, d = v0, d0
  out: list = []
  last: Any = None
  for k in range(n):
    if k >= brake_at and (brake_n is None or k < brake_at + brake_n):
      v = max(0.0, v + a_brake * DT_MDL)
    d += (v - v0) * DT_MDL
    if k % 2 == 0:
      vr = v - v0 + (noise(k) if noise else 0.0)
      last = (round(d * 128) / 128, round(vr * 64) / 64)
    dr, vr = last
    if tr is None:
      tr = Track(tid, vr + v0, kp)
    tr.update(dr, 0.0, vr, vr + v0)
    f = SimpleNamespace(**tr.get_RadarState(1.0))
    f.camera = None if cam_a is None else cam_lead(d, cam_a if k >= brake_at + cam_lag else 0.0)
    out.append(f)
  return out


def readings(vs, d0=30.0, v_ego=V0, a=-1.5, cam_a=-1.0, tid=7):
  """radarState frames at 20 Hz from the lead's 10 Hz radar speed readings vs (each on two frames), radard's aLeadK
  held at a, the camera's lead at the radar's range with accel cam_a."""
  frames, d = [], d0
  for i, v in enumerate(vs):
    if i:
      d += ((v + vs[i - 1]) / 2 - v_ego) * lba.RADAR_DT
    f = lead(tid=tid, d=d, v_lead=v, a=a, v_ego=v_ego)
    f.camera = cam_lead(d, cam_a)
    frames += [f, f]
  return frames


def run(frames, cfg=None, v_ego=V0):
  a = lba.LeadBrakeAssist(cfg)
  return [a.update(f, (k + 1) * DT_MDL, v_ego, model_lead=getattr(f, 'camera', None)) for k, f in enumerate(frames)]


# Logged radarState leadOne around three triggers of the first version, radard on the logged radar CAN, with the
# camera's lead: (t s, dRel, vRel, vLead, aLeadK, aLeadTau, camera x, camera a, vEgo) every 20 Hz frame.
# fd t=662.96 (engaged, 23.4 m/s, 37 m): a jump to ~20 m/s for 0.7 s and back to 22.5 - the lead never braked.
FD_GLITCH = (1439, [
  (-1.8, 38.0938, -1.5938, 22.6402, -0.2931, 1.5, 39.97, 0.001, 24.2), (-1.75, 38.0938, -1.5938, 22.6182, -0.3095, 1.5, 40.18, 0.017, 24.188),
  (-1.7, 37.9922, -1.5938, 22.6067, -0.3217, 1.5, 39.23, 0.0, 24.169), (-1.65, 37.9922, -1.5938, 22.5939, -0.3307, 1.5, 39.15, -0.007, 24.152),
  (-1.6, 37.7891, -1.5938, 22.5752, -0.3387, 1.5, 39.75, 0.013, 24.122), (-1.55, 37.7891, -1.5938, 22.5582, -0.3453, 1.5, 39.26, 0.001, 24.115),
  (-1.5, 37.6875, -1.5938, 22.5281, -0.3542, 1.5, 40.08, 0.018, 24.096), (-1.45, 37.6875, -1.5938, 22.5216, -0.3584, 1.5, 39.65, 0.002, 24.078),
  (-1.4, 37.5938, -1.5938, 22.5023, -0.3622, 1.5, 39.6, -0.009, 24.065), (-1.35, 37.5938, -1.5938, 22.484, -0.3653, 1.5, 38.5, -0.017, 24.026),
  (-1.3, 37.3906, -1.2969, 22.7686, -0.2814, 1.5, 39.04, -0.011, 24.023), (-1.25, 37.3906, -1.2969, 22.7293, -0.2201, 1.5, 38.87, -0.009, 23.988),
  (-1.2, 37.3906, -1.5938, 22.4294, -0.2527, 1.5, 38.69, -0.014, 23.977), (-1.15, 37.3906, -1.5938, 22.3939, -0.2858, 1.5, 37.41, -0.032, 23.957),
  (-1.1, 37.5938, -1.2969, 22.6798, -0.227, 1.5, 39.53, -0.009, 23.928), (-1.05, 37.5938, -1.2969, 22.6605, -0.1814, 1.5, 37.68, -0.024, 23.916),
  (-1.0, 37.4922, -1.5938, 22.3341, -0.2348, 1.5, 39.22, -0.017, 23.884), (-0.95, 37.4922, -1.5938, 22.3227, -0.2782, 1.5, 38.95, -0.003, 23.866),
  (-0.899, 37.0938, -1.5, 22.3837, -0.2923, 1.5, 39.82, 0.0, 23.848), (-0.848, 37.0938, -1.5, 22.366, -0.3046, 1.5, 36.35, -0.012, 23.833),
  (-0.8, 36.7812, -1.5938, 22.2546, -0.3421, 1.5, 37.59, -0.012, 23.82), (-0.75, 36.7812, -1.5938, 22.2394, -0.3722, 1.5, 36.94, -0.016, 23.776),
  (-0.7, 37.8828, -1.7031, 22.1164, -0.4265, 1.5, 37.91, -0.024, 23.771), (-0.65, 37.8828, -1.7031, 22.0731, -0.477, 1.5, 36.6, -0.034, 23.73),
  (-0.6, 37.4766, -4.0, 19.7706, -1.1689, 1.35, 37.29, -0.034, 23.708), (-0.55, 37.4766, -4.0, 19.7303, -1.7279, 1.215, 38.21, -0.037, 23.696),
  (-0.5, 36.9922, -2.9062, 20.8017, -1.8531, 1.0935, 38.74, -0.032, 23.668), (-0.45, 36.9922, -2.9062, 20.7897, -1.9322, 0.9841, 37.5, -0.03, 23.651),
  (-0.4, 36.7891, -2.9062, 20.7615, -1.9771, 0.8857, 36.9, -0.039, 23.618), (-0.35, 36.7891, -2.9062, 20.7451, -1.9902, 0.7972, 36.52, -0.029, 23.595),
  (-0.3, 36.5938, -3.0, 20.6177, -2.0089, 0.7174, 36.28, -0.017, 23.577), (-0.25, 36.5938, -3.0, 20.5954, -2.0017, 0.6457, 36.45, -0.016, 23.55),
  (-0.2, 36.5938, -3.7031, 19.8734, -2.1735, 0.5811, 35.32, -0.015, 23.536), (-0.15, 36.5938, -3.7031, 19.8471, -2.2901, 0.523, 35.67, -0.009, 23.499),
  (-0.1, 36.6875, -3.7031, 19.8328, -2.3565, 0.4707, 36.66, -0.029, 23.485), (-0.05, 36.6875, -3.7031, 19.7961, -2.3875, 0.4236, 35.82, -0.023, 23.454),
  (0.0, 36.5938, -4.0938, 19.3917, -2.4942, 0.3813, 35.4, -0.051, 23.434), (0.05, 36.5938, -4.0938, 19.36, -2.5546, 0.3432, 36.33, -0.034, 23.413),
  (0.1, 35.7891, -0.9062, 22.5282, -1.6627, 0.3088, 36.04, -0.038, 23.378), (0.15, 35.7891, -0.9062, 22.5064, -0.9179, 0.278, 37.82, -0.027, 23.363),
  (0.201, 35.0938, -1.2031, 22.1753, -0.3921, 1.5, 35.42, -0.044, 23.332), (0.251, 35.0938, -1.2031, 22.1595, 0.0378, 1.5, 34.22, -0.055, 23.317),
  (0.301, 34.6875, -0.9062, 22.426, 0.4639, 1.5, 33.48, -0.054, 23.291)])
# c8 t=1449.31 (engaged, 25 m/s, 75 m): three readings dip 1.8 m/s and snap back while the range keeps closing steadily.
C8_GLITCH = (3193, [
  (-1.8, 80.2969, -2.4062, 22.4068, 0.4297, 1.5, 84.79, -0.001, 24.823), (-1.75, 80.1016, -2.4062, 22.4094, 0.3806, 1.5, 80.95, -0.005, 24.822),
  (-1.7, 80.1016, -2.4062, 22.417, 0.3373, 1.5, 84.3, -0.004, 24.834), (-1.651, 79.8984, -2.4062, 22.4157, 0.2968, 1.5, 80.12, -0.007, 24.868),
  (-1.6, 79.8984, -2.4062, 22.4281, 0.2631, 1.5, 76.02, -0.004, 24.862), (-1.55, 79.8984, -0.7031, 24.1648, 0.7278, 1.35, 77.33, -0.003, 24.867),
  (-1.5, 79.8984, -0.7031, 24.1591, 1.0947, 1.215, 80.2, -0.008, 24.873), (-1.45, 79.6016, -1.5, 23.3666, 1.1519, 1.0935, 86.57, 0.0, 24.879),
  (-1.4, 79.6016, -1.5, 23.3729, 1.1839, 0.9841, 86.53, -0.003, 24.885), (-1.351, 79.2031, -3.2969, 21.5822, 0.6818, 0.8857, 76.28, 0.019, 24.883),
  (-1.3, 79.2031, -3.2969, 21.5883, 0.2643, 1.5, 79.27, 0.01, 24.881), (-1.25, 78.5, -2.7031, 22.1801, 0.0892, 1.5, 79.05, -0.007, 24.88),
  (-1.2, 78.5, -2.7031, 22.1782, -0.0555, 1.5, 81.13, -0.007, 24.877), (-1.15, 78.2031, -2.7969, 22.0835, -0.1997, 1.5, 77.01, -0.001, 24.888),
  (-1.1, 78.2031, -2.7969, 22.0804, -0.3153, 1.5, 79.56, 0.015, 24.883), (-1.05, 77.7969, -3.0938, 21.7943, -0.4868, 1.5, 75.19, 0.019, 24.899),
  (-0.999, 77.7969, -3.0938, 21.7895, -0.621, 1.35, 76.0, 0.034, 24.907), (-0.95, 77.7031, -3.0938, 21.8056, -0.717, 1.215, 76.81, 0.028, 24.916),
  (-0.9, 77.7031, -3.0938, 21.8134, -0.7828, 1.0935, 76.15, 0.045, 24.931), (-0.85, 77.7031, -2.0938, 22.8219, -0.5373, 0.9841, 78.96, 0.03, 24.936),
  (-0.801, 77.7031, -2.0938, 22.8374, -0.3251, 1.5, 80.03, 0.019, 24.945), (-0.749, 77.6016, -2.5938, 22.3426, -0.2886, 1.5, 78.15, 0.004, 24.949),
  (-0.7, 77.6016, -2.5938, 22.3513, -0.2523, 1.5, 75.47, 0.035, 24.952), (-0.649, 77.3984, -2.5938, 22.3556, -0.2178, 1.5, 78.89, 0.004, 24.964),
  (-0.599, 77.3984, -2.5938, 22.3585, -0.1858, 1.5, 78.0, 0.005, 24.961), (-0.55, 77.2031, -2.4062, 22.5582, -0.1, 1.5, 73.79, -0.004, 24.962),
  (-0.5, 77.2031, -2.4062, 22.555, -0.0296, 1.5, 77.98, -0.007, 24.966), (-0.45, 76.5, -2.9062, 22.0553, -0.1144, 1.5, 73.44, 0.015, 24.975),
  (-0.4, 76.5, -2.9062, 22.0596, -0.1807, 1.5, 75.38, 0.012, 24.999), (-0.35, 76.1016, -2.5938, 22.3813, -0.1403, 1.5, 76.96, -0.01, 25.011),
  (-0.3, 76.1016, -2.5938, 22.4049, -0.0986, 1.5, 78.69, -0.022, 25.024), (-0.25, 75.6016, -3.5938, 21.4174, -0.3452, 1.5, 76.64, -0.007, 25.027),
  (-0.2, 75.6016, -3.5938, 21.4303, -0.5376, 1.35, 71.97, -0.015, 25.018), (-0.15, 75.5, -4.0, 21.027, -0.8021, 1.215, 73.07, 0.007, 25.031),
  (-0.1, 75.5, -4.0, 21.0175, -1.0089, 1.0935, 69.21, 0.021, 25.031), (-0.05, 75.5, -4.4062, 20.6245, -1.2754, 0.9841, 70.88, -0.013, 25.024),
  (0.0, 75.5, -4.4062, 20.6243, -1.4746, 0.8857, 68.4, -0.013, 25.024), (0.049, 74.8828, -2.7031, 22.3213, -1.1313, 0.7972, 76.47, -0.028, 25.007),
  (0.1, 74.8828, -2.7031, 22.3206, -0.8355, 0.7174, 74.71, -0.049, 25.002), (0.15, 74.4766, -3.2031, 21.8044, -0.7298, 0.6457, 77.91, -0.031, 24.982),
  (0.2, 74.4766, -3.2031, 21.799, -0.6347, 0.5811, 74.64, -0.039, 24.969), (0.25, 73.8984, -3.2031, 21.7791, -0.5537, 0.523, 88.45, -0.024, 24.959),
  (0.299, 73.6016, -3.2031, 21.7654, -0.4837, 1.5, 81.85, -0.021, 24.929)])

# 12a t=3196.49 (engaged, 8 m/s, 15 m; the first version's own replay of radard): a lead easing off at about -1,
# readings 8.09, 8.02, 7.88, 7.79, then one at 6.81 and back to 7.67 - the camera saw it easing (-0.25 to -0.38).
R12A_DROP = (8460, [
  (-1.802, 16.5, -1.0938, 9.5414, -1.1014, 0.0001, 17.89, -1.099, 10.512), (-1.75, 16.3984, -1.2031, 9.3694, -1.088, 0.0001, 17.8, -1.134, 10.428),
  (-1.699, 16.3984, -1.2031, 9.3086, -1.0789, 0.0001, 17.13, -0.965, 10.352), (-1.649, 16.2969, -1.0, 9.428, -1.022, 0.0, 16.52, -0.89, 10.284),
  (-1.6, 16.2969, -1.0, 9.3524, -0.9826, 0.0, 15.77, -0.868, 10.214), (-1.554, 16.1016, -1.2031, 9.0806, -1.014, 0.0, 15.7, -0.828, 10.149),
  (-1.5, 16.1016, -1.2031, 9.0104, -1.0452, 0.0, 15.78, -0.896, 10.053), (-1.449, 16.0, -1.0, 9.1486, -1.0163, 0.0, 16.09, -0.852, 9.983),
  (-1.399, 16.0, -1.0, 9.0533, -1.0054, 0.0, 15.23, -0.693, 9.907), (-1.352, 15.8984, -1.2031, 8.7797, -1.0603, 0.0, 15.07, -0.719, 9.845),
  (-1.299, 15.8984, -1.2031, 8.7038, -1.1116, 0.0, 15.79, -0.821, 9.759), (-1.252, 15.8984, -1.0, 8.8453, -1.0972, 0.0, 14.69, -0.718, 9.703),
  (-1.2, 15.8984, -1.0, 8.7585, -1.0945, 0.0, 14.95, -0.769, 9.642), (-1.149, 15.7031, -1.0938, 8.6093, -1.1193, 0.0, 14.9, -0.763, 9.579),
  (-1.1, 15.7031, -1.0938, 8.5482, -1.141, 0.0, 14.91, -0.721, 9.51), (-1.049, 15.6016, -0.9062, 8.673, -1.1067, 0.0, 14.78, -0.845, 9.433),
  (-1.0, 15.6016, -0.9062, 8.6041, -1.0827, 0.0, 15.36, -0.864, 9.384), (-0.949, 15.6016, -1.0, 8.4334, -1.0964, 0.0, 15.52, -0.916, 9.318),
  (-0.899, 15.6016, -1.0, 8.3841, -1.1059, 0.0, 15.78, -0.776, 9.238), (-0.85, 15.5, -0.7969, 8.5211, -1.0589, 0.0, 15.47, -0.699, 9.167),
  (-0.799, 15.5, -0.7969, 8.441, -1.0282, 0.0, 15.56, -0.634, 9.114), (-0.75, 15.2969, -0.9062, 8.2606, -1.04, 0.0, 15.91, -0.64, 9.055),
  (-0.702, 15.2969, -0.9062, 8.2074, -1.05, 0.0, 15.6, -0.682, 8.995), (-0.65, 15.1875, -0.7969, 8.2584, -1.0286, 0.0, 16.14, -0.714, 8.922),
  (-0.6, 15.1875, -0.7969, 8.1985, -1.0136, 0.0, 16.3, -0.771, 8.86), (-0.549, 15.0781, -0.7969, 8.1252, -1.0078, 0.0, 15.62, -0.759, 8.79),
  (-0.502, 15.0781, -0.7969, 8.063, -1.0064, 0.0, 15.76, -0.496, 8.721), (-0.452, 14.9688, -0.4062, 8.3836, -0.8994, 0.0, 15.74, -0.483, 8.633),
  (-0.4, 14.9688, -0.4062, 8.315, -0.8189, 0.0, 15.42, -0.376, 8.556), (-0.35, 14.8594, -0.5938, 8.0396, -0.8202, 0.0, 14.38, -0.275, 8.495),
  (-0.299, 14.8594, -0.5938, 7.962, -0.8317, 0.0, 14.99, -0.187, 8.431), (-0.254, 14.7422, -0.4062, 8.0889, -0.793, 0.0, 14.83, -0.259, 8.379),
  (-0.202, 14.7422, -0.4062, 8.0243, -0.7685, 0.0, 15.36, -0.379, 8.291), (-0.153, 14.6328, -0.5, 7.8788, -0.7791, 0.0, 15.08, -0.345, 8.219),
  (-0.1, 14.6328, -0.5, 7.7915, -0.8016, 0.0, 15.74, -0.153, 8.151), (-0.051, 14.5234, -1.4062, 6.8126, -1.088, 0.0, 16.28, -0.251, 8.076),
  (0.0, 14.5234, -1.4062, 6.7448, -1.3254, 0.0, 15.73, -0.145, 8.001), (0.051, 14.4922, -0.4062, 7.67, -1.2358, 0.0, 14.91, -0.069, 7.926),
  (0.102, 14.4922, -0.4062, 7.5946, -1.1667, 0.0, 15.34, 0.05, 7.925), (0.151, 14.3672, 0.0, 7.9259, -0.999, 0.0, 15.66, -0.101, 7.918),
  (0.201, 14.3672, 0.0, 7.925, -0.8483, 0.0, 15.89, -0.044, 7.919), (0.25, 14.3438, -0.2031, 7.7153, -0.7732, 0.0, 16.11, 0.119, 7.934),
  (0.3, 14.3438, -0.2031, 7.7155, -0.7008, 0.0, 16.11, 0.176, 7.938)])


def logged(seq, cfg=None):
  tid, rows = seq
  a = lba.LeadBrakeAssist(cfg)
  out = []
  for (t, d, v_rel, v_lead, a_k, tau, cx, ca, v_ego) in rows:
    f = lead(tid=tid, d=d, v_lead=v_lead, a=a_k, tau=tau, v_ego=v_ego)
    f.vRel = v_rel   # radard's, as logged (vLead is vRel plus the radar-delayed vEgo)
    out.append(a.update(f, 100.0 + t, v_ego, model_lead=cam_lead(cx, ca)))
  return out


class TestProjection(OpenpilotTestCase):
  def test_defaults(self):
    c = lba.Config()
    self.assertEqual((c.raw_thr, c.raw_win, c.raw_thr2, c.raw_win2, c.raw_d_max, c.raw_kalman, c.raw_v_moving),
                     (-2.0, 0.3, -1.5, 0.5, 60.0, -1.0, 4.0))
    self.assertEqual((c.raw_n, c.raw_n_alone, c.raw_cam_a, c.raw_cam_win, c.raw_cam_prob, c.raw_one_step), (1, 2, -0.25, 0.3, 0.5, 0.5))
    self.assertTrue(c.use_raw and c.use_tau and c.tau_from_proj and c.tau_needs_raw and c.tau_release)
    self.assertFalse(c.raw_frames or c.raw_gap_gate)
    self.assertEqual(lba.MODE_DEFAULT, lba.MODE_SHADOW)

  def test_a_hard_braking_lead_is_projected_sooner_than_radards_kalman(self):
    frames = braking()
    ps = run(frames)
    first = next(k for k, p in enumerate(ps) if p.active)
    k_minus2 = next(k for k, f in enumerate(frames) if f.aLeadK < -2.0)
    self.assertLess(first, k_minus2, "the projection starts before radard's aLeadK reaches -2")
    p = ps[first]
    self.assertTrue(p.reason & lba.REASON_RAW and p.cam)
    self.assertAlmostEqual(p.a_lead, -4.0, delta=0.4)
    self.assertLess(p.a_lead, p.a_lead_k)
    # the projection never claims less braking than radard does
    self.assertTrue(all(q.a_lead <= q.a_lead_k + 1e-12 for q in ps))
    self.assertTrue(all(q.tau <= q.a_lead_tau + 1e-12 for q in ps))
    # without the camera the fall must show on two radar samples in a row; here the first one under the thresholds was
    # still blocked by radard's aLeadK, so it costs nothing - and the projection still starts before radard's -2
    alone = run(braking(cam_a=None))
    first_alone = next(k for k, p in enumerate(alone) if p.active)
    self.assertGreaterEqual(first_alone, first)
    self.assertLess(first_alone, k_minus2)
    self.assertTrue(any(p.blocked & lba.BLOCK_CAMERA for p in alone))

  def test_ordinary_following_is_untouched(self):
    rng = np.random.default_rng(0)
    frames = braking(n=200, brake_at=10_000, noise=lambda k: float(rng.normal(0.0, 0.05)))
    self.assertFalse(any(p.active for p in run(frames)))
    frames = braking(n=120, brake_at=20, a_brake=-1.2)   # a lead easing off at -1.2: not hard braking
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in run(frames)))

  def test_vision_held_and_absent_leads_are_never_projected(self):
    for kw in ({'radar': False, 'tid': -1}, {'tid': held_id(7)}, {'present': False}):
      frames = [SimpleNamespace(**{**vars(f), **{k: v for k, v in kw.items() if k != 'tid'},
                                   'radarTrackId': kw.get('tid', f.radarTrackId)}) for f in braking()]
      ps = run(frames)
      self.assertFalse(any(p.active or p.reason for p in ps), kw)
      self.assertTrue(all(p.a_lead == p.a_lead_k and p.tau == p.a_lead_tau for p in ps), kw)

  def test_a_track_never_seen_moving_is_blocked(self):
    # stationary returns (clutter, a stopped car) and oncoming traffic: never read 4 m/s while leadOne
    frames = braking(v0=3.5, a_brake=-3.0)
    ps = run(frames, v_ego=3.5)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in ps))
    self.assertTrue(any(p.blocked & lba.BLOCK_SLOW for p in ps))
    oncoming = [lead(d=60.0 - k, v_lead=-8.0 - 0.3 * k, a=-2.5) for k in range(40)]
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in run(oncoming)))

  def test_far_leads_are_blocked(self):
    # beyond 60 m (80 m in the first version: c8's and de's speed dips were 73-76 m out)
    for d0 in (110.0, 70.0):
      ps = run(braking(n=40, d0=d0))
      self.assertFalse(any(p.reason & lba.REASON_RAW for p in ps), d0)
      self.assertTrue(any(p.blocked & lba.BLOCK_RANGE for p in ps), d0)
    self.assertTrue(any(p.reason & lba.REASON_RAW for p in run(braking(n=40, d0=55.0))))

  def test_a_speed_jump_is_not_a_braking_car(self):
    # one radar reading 2 m/s low (a Doppler glitch), then back: the samples do not fall one after another
    frames = braking(n=80, brake_at=10_000, noise=lambda k: -2.0 if k == 40 else 0.0)
    ps = run(frames)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in ps))
    # a step down that stays (another object's speed, 25 m/s^2 in one reading): the window restarts from it
    frames = braking(n=80, brake_at=10_000, noise=lambda k: -2.5 if k >= 40 else 0.0)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in run(frames)))

  def test_the_samples_must_fall_one_after_another(self):
    # a lead slowing hard, but one of its last three radar steps rises 0.15 m/s: that reading does not count, though
    # both windows' slopes are under their thresholds, radard's aLeadK is under -1 and the camera sees it braking
    rise = readings([20.0] * 8 + [19.7, 19.3, 18.9, 18.4, 18.55, 17.9])
    fall = readings([20.0] * 8 + [19.7, 19.3, 18.9, 18.4, 18.15, 17.9])
    p_rise, p_fall = run(rise)[-2], run(fall)[-2]   # the frame the last reading (17.9) arrives
    self.assertLess(p_rise.slope, -2.0)
    self.assertTrue(p_rise.blocked & lba.BLOCK_MONO)
    self.assertFalse(p_rise.reason & lba.REASON_RAW)
    self.assertTrue(p_fall.reason & lba.REASON_RAW and not p_fall.blocked)

  def test_radards_own_kalman_must_agree(self):
    cfg = lba.Config()
    frames = braking()
    for f in frames:
      f.aLeadK = max(f.aLeadK, -0.9)     # radard's estimate never under -1.0
    ps = run(frames, cfg)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in ps))
    self.assertTrue(any(p.blocked & lba.BLOCK_KALMAN for p in ps))

  def test_a_fall_the_camera_does_not_see_needs_a_second_sample(self):
    # the lead slowing at about -1.5, then two readings falling faster and back up: with the camera agreeing the second
    # of them counts; without it, the next reading (back up) ends the fall before it counts
    vs = [20.0] * 8 + [19.85, 19.7, 19.55, 19.25, 18.9, 19.4, 19.3]
    for cam_a, want in ((-1.0, True), (0.0, False)):
      ps = run(readings(vs, cam_a=cam_a))
      self.assertEqual(any(p.reason & lba.REASON_RAW for p in ps), want, cam_a)
    blocked = run(readings(vs, cam_a=0.0))[-6]   # the frame of the reading at 18.9
    self.assertTrue(blocked.blocked & lba.BLOCK_CAMERA and not blocked.cam)
    # a fall that goes on counts without the camera too, one radar sample later
    goes_on = [20.0] * 8 + [19.85, 19.7, 19.55, 19.25, 18.9, 18.5, 18.1]
    first = {cam_a: next(k for k, p in enumerate(run(readings(goes_on, cam_a=cam_a))) if p.reason & lba.REASON_RAW) for cam_a in (-1.0, 0.0)}
    self.assertEqual((first[-1.0], first[0.0]), (24, 26))
    # the camera's lead must be the radar's lead: another car (twice the range) braking says nothing about this one
    other = readings(vs, cam_a=-2.0)
    for f in other:
      f.camera = cam_lead(2 * f.dRel, -2.0)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in run(other)))
    # nor does a lead the camera is unsure of
    unsure = readings(vs, cam_a=-2.0)
    for f in unsure:
      f.camera = cam_lead(f.dRel, -2.0, prob=0.3)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in run(unsure)))
    self.assertTrue(all(math.isnan(p.a_cam) for p in run(unsure)))

  def test_logged_radar_speed_glitches_are_not_braking(self):
    # route fd t=662.96: engaged at 23.4 m/s, the lead 37 m ahead read ~20 m/s for 0.7 s and then 22.5 again; the first
    # version projected -3.7 on two frames (the plan 0.26 firmer for about a second). The camera saw no braking.
    old = logged(FD_GLITCH, lba.Config(**OLD))
    self.assertTrue(any(p.reason & lba.REASON_RAW for p in old), "the first version braked for it")
    new = logged(FD_GLITCH)
    self.assertFalse(any(p.active or p.reason for p in new))
    self.assertTrue(any(p.blocked & lba.BLOCK_CAMERA for p in new))
    # c8 t=1449.31: engaged at 25 m/s, three readings 1.8 m/s low 75 m out (-5.7 projected); now out of range
    self.assertTrue(any(p.reason & lba.REASON_RAW for p in logged(C8_GLITCH, lba.Config(**OLD))))
    new = logged(C8_GLITCH)
    self.assertFalse(any(p.active or p.reason for p in new))
    self.assertTrue(any(p.blocked & lba.BLOCK_RANGE for p in new))

  def test_a_fall_resting_on_one_reading_needs_a_second_one(self):
    # 12a: one reading 1 m/s low on a lead easing off at about -1 made the first version project -3.9 for a sample (the
    # plan -1.82 against -1.25). The camera does see the lead easing off, so its agreement is not enough here: a fall
    # carried by one reading must show on the next one too, and the next one is back up
    self.assertTrue(any(p.reason & lba.REASON_RAW for p in logged(R12A_DROP, lba.Config(**OLD))), "the first version")
    self.assertTrue(any(p.reason & lba.REASON_RAW for p in logged(R12A_DROP, lba.Config(raw_one_step=0.0))), "the camera agrees")
    new = logged(R12A_DROP)
    self.assertFalse(any(p.active or p.reason for p in new))
    self.assertTrue(any(p.blocked & lba.BLOCK_CAMERA and p.cam for p in new))
    # the rule itself: the newest step 0.5 m/s or more down, the two before it each under half of it
    one = [(0.0, 8.09, 0.0), (0.1, 8.02, 0.0), (0.2, 7.88, 0.0), (0.3, 7.79, 0.0), (0.4, 6.81, 0.0)]
    self.assertTrue(lba._one_reading(one, 0.5))
    self.assertFalse(lba._one_reading(one[:-1], 0.5))                                    # no big step
    self.assertFalse(lba._one_reading([(0, 9.6, 0), (0, 9.2, 0), (0, 8.8, 0), (0, 8.3, 0)], 0.5))   # a steady hard fall
    self.assertFalse(lba._one_reading(one, 0.0))                                         # off
    # a hard brake that starts with one big step still counts as soon as the fall goes on (the step is not the newest)
    vs = [20.0] * 8 + [19.9, 19.85, 19.2, 18.7, 18.2, 17.7]
    ps = run(readings(vs, cam_a=-1.5))
    k = next(k for k, p in enumerate(ps) if p.reason & lba.REASON_RAW)
    self.assertEqual(k, 2 * (len(vs) - 3))   # 18.7, the reading after the big step (19.85 -> 19.2)

  def test_a_repeated_radar_reading_is_one_sample(self):
    # radard runs at 20 Hz on a 10 Hz radar: a reading repeated on the next frame is the same sample; one that is still
    # the same a radar period later is a new sample of a lead that kept its distance and speed
    a = lba.LeadBrakeAssist()
    f = lead(d=30.0, v_lead=18.0)
    a.update(f, DT_MDL, V0)
    a.update(f, 2 * DT_MDL, V0)
    self.assertEqual(a.track.n, 1)
    a.update(f, 3 * DT_MDL, V0)
    self.assertEqual(a.track.n, 2)
    for k in range(4, 24):
      a.update(f, k * DT_MDL, V0)
    self.assertEqual(a.track.n, 12)

  def test_a_new_track_or_a_dropout_restarts(self):
    frames = braking()
    a = lba.LeadBrakeAssist()
    for k, f in enumerate(frames[:30]):
      a.update(f, (k + 1) * DT_MDL, V0, model_lead=f.camera)
    swapped = [SimpleNamespace(**{**vars(f), 'radarTrackId': 8}) for f in frames[30:]]
    ps2 = [a.update(f, (k + 31) * DT_MDL, V0, model_lead=f.camera) for k, f in enumerate(swapped)]
    self.assertEqual(a.track.tid, 8)
    first = next(k for k, p in enumerate(ps2) if p.reason & lba.REASON_RAW)
    self.assertGreaterEqual(first, 2 * 5, "a new track needs its own 0.5 s of samples")
    # a 0.3 s dropout of new samples clears the window
    a = lba.LeadBrakeAssist()
    for k, f in enumerate(frames[:40]):
      a.update(f, (k + 1) * DT_MDL, V0)
    a.update(frames[40], 47 * DT_MDL, V0)
    self.assertEqual(len(a.track.samples), 1)

  def test_non_finite_and_malformed_leads(self):
    a = lba.LeadBrakeAssist()
    for bad in (lead(d=math.nan), lead(a=math.inf), lead(tau=math.nan), SimpleNamespace(present=True)):
      p = a.update(bad, 1.0, V0)
      self.assertFalse(p.active)
    self.assertFalse(a.update(lead(), math.nan, V0).active)
    for cam in (cam_lead(math.nan, -2.0), cam_lead(30.0, math.nan), SimpleNamespace(prob=1.0, x=[], a=[]), SimpleNamespace()):
      p = a.update(lead(d=30.0), 2.0, V0, model_lead=cam)
      self.assertFalse(p.cam)
      self.assertTrue(math.isnan(p.a_cam))

  def test_the_tau_attack_needs_two_distinct_samples_and_never_prolongs_an_acceleration(self):
    cfg = lba.Config(use_raw=False, use_tau=True, tau_from_proj=False, tau_needs_raw=False)   # CarrotPilot's own
    tb = lba._TauBoost(cfg)
    self.assertEqual(tb.update(-2.0, -4.0, 1.0, True), 1.0)          # one sample: ordinary decay only
    self.assertEqual(tb.update(-2.0, -4.0, 1.0, False), 1.0)         # the same sample again: no confirmation
    b = tb.update(-2.0, -4.0, 1.1, True)                             # a second, distinct one 0.1 s later: the attack
    self.assertAlmostEqual(b, (1.0 - DT_MDL / (cfg.tau_attack_rc + DT_MDL)) / (1.0 - lba.LEAD_ACCEL_ALPHA), places=9)
    self.assertEqual(tb.update(-2.0, -4.0, 1.1, False), b)           # the frame between samples: radard's decay only
    self.assertEqual(tb.update(0.3, -4.0, 1.2, True), 1.0)           # accelerating: radard's tau back at once
    tb = lba._TauBoost(cfg)
    tb.update(-2.0, -4.0, 1.0, True)
    self.assertEqual(tb.update(-2.0, -4.0, 1.3, True), 1.0)          # 0.3 s apart: too far to confirm
    # end to end: the attack shortens radard's aLeadTau on a hard-braking lead, and only while the lead brakes
    ps = run(braking(), cfg)
    self.assertTrue(any(p.reason & lba.REASON_TAU for p in ps))
    self.assertTrue(all(p.a_lead == p.a_lead_k for p in ps))
    # the default: the attack only on samples where the raw decel counts, so never on a lead the raw decel leaves alone
    ps = run(braking(), lba.Config(raw_kalman=-10.0))
    self.assertFalse(any(p.reason for p in ps))
    tb = lba._TauBoost(lba.Config())
    tb.update(-2.0, -4.0, 1.0, True, allowed=False)
    self.assertEqual(tb.update(-2.0, -4.0, 1.1, True, allowed=False), 1.0)

  def test_a_braking_that_ended_is_not_prolonged(self):
    # the lead brakes at -3.5 for 0.6 s and then holds its speed. radard's aLeadK lags it; the attack's shortened
    # aLeadTau would keep that lagging aLeadK in the prediction until it recovers past -0.5 (about a second of a firmer
    # plan behind a lead at a steady speed). Once the lead's raw speed stops falling, radard's own aLeadTau is back.
    frames = braking(n=100, a_brake=-3.5, brake_n=12)
    end = 20 + 12
    for cfg, want_tail in ((lba.Config(**cast(dict[str, Any], {**OLD, 'raw_cam_a': lba.Config().raw_cam_a})), True), (lba.Config(), False)):
      ps = run(frames, cfg)
      self.assertTrue(any(p.reason & lba.REASON_RAW for p in ps[:end + 4]))
      late = [k for k in range(end + 8, len(ps)) if frames[k].aLeadK < -0.5]
      self.assertTrue(late, "radard's aLeadK still under -0.5 0.4 s after the lead stopped braking")
      tail = [k for k in late if ps[k].reason & lba.REASON_TAU]
      self.assertEqual(bool(tail), want_tail, cfg.tau_release)
    self.assertFalse(any(ps[k].active for k in late))

  def test_quadratic_jerk_weights(self):
    for n in (3, 6, 7, 9):
      w = np.array(lba.quadratic_jerk_weights(n))
      ts = np.arange(n) - (n - 1.0)
      self.assertAlmostEqual(float(w @ (0.5 * 3.0 * ts ** 2 + 2.0 * ts + 1.0)), 3.0, places=9)   # 2nd derivative of a parabola

  def test_predicted_dv(self):
    self.assertAlmostEqual(lba.predicted_dv(0.0, 1.5), 0.0)
    self.assertLess(lba.predicted_dv(-2.0, 0.0), lba.predicted_dv(-2.0, 1.5))
    self.assertAlmostEqual(lba.predicted_dv(-1.0, 0.0), -4.1, places=6)



class TestModeAndGate(OpenpilotTestCase):
  def _cp(self, fp=CAR.HONDA_ACCORD_9G_AU, oplong=True):
    CP = structs.CarParams()
    CP.brand, CP.carFingerprint, CP.openpilotLongitudinalControl = 'honda', fp, oplong
    return CP

  def test_this_car_with_openpilot_longitudinal_only(self):
    sp = custom.CarParamsSP.new_message().as_reader()
    self.assertTrue(lba.lead_brake_applies(self._cp(), sp))
    self.assertFalse(lba.lead_brake_applies(self._cp(CAR.HONDA_CIVIC), sp))
    self.assertFalse(lba.lead_brake_applies(self._cp(oplong=False), sp))
    stock = custom.CarParamsSP.new_message()
    stock.flags = HondaFlagsSP.ELESYS_STOCK_ACC.value
    self.assertFalse(lba.lead_brake_applies(self._cp(), stock.as_reader()))
    self.assertFalse(lba.lead_brake_applies(None, None))

  def test_the_param(self):
    params = Params()
    params.remove(lba.PARAM)
    self.assertEqual(lba.read_mode(), lba.MODE_SHADOW, "default 1: shadow")
    for m in (0, 1, 2):
      params.put(lba.PARAM, m, block=True)
      self.assertEqual(lba.read_mode(), m)
    self.assertEqual(lba.read_mode(SimpleNamespace(get=lambda *a, **k: 7)), lba.MODE_SHADOW)
    self.assertEqual(lba.read_mode(SimpleNamespace(get=mock.Mock(side_effect=RuntimeError))), lba.MODE_SHADOW)
    keys = {k.decode() if isinstance(k, bytes) else k for k in params.all_keys()}
    self.assertTrue(lba.PARAM in keys)
    params.remove(lba.PARAM)

  def test_sunnylink_can_switch_it(self):
    # no SSH to the car: sunnylink's Vehicle > Honda page sets it, offroad only (plannerd reads it once per drive)
    from openpilot.sunnypilot import sunnylink
    with open(os.path.join(os.path.dirname(sunnylink.__file__), 'settings_ui.json')) as f:
      ui = json.load(f)
    items = {i['key']: i for i in ui['vehicle_settings']['honda']['items']}
    item = items[lba.PARAM]
    self.assertEqual(item['widget'], 'multiple_button')
    self.assertEqual([o['value'] for o in item['options']], [lba.MODE_OFF, lba.MODE_SHADOW, lba.MODE_LIVE])
    self.assertTrue({'type': 'offroad_only'} in item['enablement'])
    self.assertTrue(item['needs_onroad_cycle'])

  def test_mode_0_and_other_cars_build_nothing(self):
    sp = custom.CarParamsSP.new_message().as_reader()
    self.assertIsNone(lba.lead_brake_planner(self._cp(), sp, mode=lba.MODE_OFF))
    self.assertIsNone(lba.lead_brake_planner(self._cp(CAR.HONDA_CIVIC), sp, mode=lba.MODE_LIVE))
    self.assertIsInstance(lba.lead_brake_planner(self._cp(), sp, mode=lba.MODE_SHADOW), lba.LeadBrakePlanner)

  def test_the_log_struct_and_service(self):
    from openpilot.cereal.services import SERVICE_LIST
    self.assertTrue('customReserved13' in SERVICE_LIST)
    self.assertEqual(SERVICE_LIST['customReserved13'].decimation, 1, "every message it sends reaches the qlog")
    msg = messaging.new_message('customReserved13')
    names = set(msg.customReserved13.schema.fieldnames)
    want = {'mode', 'active', 'reason', 'blocked', 'trackId', 'aLeadK', 'aLeadTau', 'aRaw', 'jLead', 'aLeadProj', 'aLeadTauProj',
            'aTarget', 'aTargetBase', 'aTargetAssist', 'fcwBase', 'fcwAssist', 'compared', 'episodes', 'skipped', 'aCamera', 'camera'}
    self.assertEqual(want - names, set())


# ---- through the real planner ----

class _SM(dict):
  def __init__(self, services):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.logMonoTime = dict.fromkeys(services, 0)
    self.updated = dict.fromkeys(services, True)
    self.recv_frame = dict.fromkeys(services, 1)

  def all_checks(self, service_list=None) -> bool:
    return True


def build_sm(ld, e2e=0.5, enabled=True, experimental=False, gas=False, a_ego=0.0, radar_mono=0):
  services = {}
  for service in ("vehicleParameters", "carStateSP", "liveMapDataSP", "gpsLocationExternal", "gpsLocation"):
    services[service] = getattr(messaging.new_message(service), service)
  rs = messaging.new_message('radarState')
  L = rs.radarState.leadOne
  for k in ('present', 'radar', 'radarTrackId', 'dRel', 'yRel', 'vRel', 'vLead', 'vLeadK', 'aLeadK', 'aLeadTau', 'modelProb'):
    setattr(L, k, getattr(ld, k))
  services['radarState'] = rs.radarState.as_reader()
  cs = messaging.new_message('carState')
  cs.carState.vEgo, cs.carState.aEgo = V0, a_ego
  cs.carState.vCruise = cs.carState.vCruiseCluster = 100.0
  cs.carState.gasPressed = gas
  services['carState'] = cs.carState.as_reader()
  ctl = messaging.new_message('controlsState')
  ctl.controlsState.longControlState = structs.CarControl.Actuators.LongControlState.pid if enabled else \
    structs.CarControl.Actuators.LongControlState.off
  services['controlsState'] = ctl.controlsState.as_reader()
  ss = messaging.new_message('selfdriveState')
  ss.selfdriveState.experimentalMode = experimental
  ss.selfdriveState.enabled = enabled
  ss.selfdriveState.personality = log.LongitudinalPersonality.aggressive
  services['selfdriveState'] = ss.selfdriveState.as_reader()
  cc = messaging.new_message('carControl')
  cc.carControl.enabled = enabled
  services['carControl'] = cc.carControl.as_reader()
  m = messaging.new_message('modelV2')
  m.modelV2.orientationRate.z = [0.01] * 33
  m.modelV2.velocity.x = [V0] * 33
  m.modelV2.position.x = [float(i) for i in range(33)]
  m.modelV2.action.desiredAcceleration = e2e
  cam = getattr(ld, 'camera', None)
  if cam is not None:
    leads = m.modelV2.init('leadsV3', 1)
    leads[0].prob, leads[0].x, leads[0].a = cam.prob, cam.x, cam.a
  services['modelV2'] = m.modelV2.as_reader()
  sm = _SM(services)
  sm.logMonoTime['radarState'] = radar_mono
  return sm


class _PM:
  def __init__(self):
    self.sent: dict = {}

  def send(self, service, msg):
    self.sent.setdefault(service, []).append(msg.as_reader() if hasattr(msg, 'as_reader') else msg)


def planner(mode, fingerprint=CAR.HONDA_ACCORD_9G_AU, cfg=None, flags=0):
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  CP = structs.CarParams()
  CP.brand = 'honda'
  CP.carFingerprint = fingerprint
  CP.openpilotLongitudinalControl = True
  CP.steerRatio = 15.0
  CP.wheelbase = 2.7
  CP.longitudinalActuatorDelay = 0.5
  CP_SP = custom.CarParamsSP.new_message()
  CP_SP.flags = flags
  # FORK(CLOSE-FOLLOW): the lead brake alone - close follow in shadow (test_lead_brake_close_follow.py has both)
  Params().put_bool("CloseFollow", False, block=True)
  with mock.patch.object(lba, 'read_mode', return_value=mode):
    pl = LongitudinalPlanner(CP, CP_SP.as_reader(), init_v=V0)
  if pl.lead_brake is not None and cfg is not None:
    pl.lead_brake.assist.cfg = cfg
  return pl


def drive(pl, frames, publish=False, sm_kw=None, **kw):
  out, pm = [], _PM()
  for k, f in enumerate(frames):
    sm = build_sm(f, **{**kw, **(sm_kw(k) if sm_kw else {})})
    pl.update(cast(Any, sm))
    out.append((float(pl.output_a_target), bool(pl.fcw), [float(x) for x in pl.a_desired_trajectory]))
    if publish:
      pl.publish(cast(Any, sm), pm)
  return out, pm


class TestPlanner(OpenpilotTestCase):
  def test_shadow_changes_nothing_and_logs_what_live_would_do(self):
    frames = braking()
    base, _ = drive(planner(lba.MODE_OFF), frames)
    pl = planner(lba.MODE_SHADOW)
    self.assertIsNotNone(pl.lead_brake)
    shadow, pm = drive(pl, frames, publish=True)
    self.assertEqual(shadow, base, "mode 1 is the plan without the assist, bit for bit")
    msgs = [m.customReserved13 for m in pm.sent['customReserved13']]
    plans = [m.longitudinalPlan for m in pm.sent['longitudinalPlan']]
    self.assertEqual(len(plans), len(frames))
    self.assertTrue(all(m.mode == lba.MODE_SHADOW for m in msgs))
    self.assertEqual({round(m.aTarget, 6) for m in msgs} - {round(p.aTarget, 6) for p in plans}, set())
    act = [m for m in msgs if m.active]
    self.assertTrue(act, "the assist fired on a lead braking at -4")
    self.assertTrue(all(a.compared and not a.skipped and a.camera for a in act))
    self.assertTrue(all(a.aTargetBase == a.aTarget or a.aTargetBase <= a.aTarget for a in act))
    self.assertLess(min(a.aTargetAssist - a.aTargetBase for a in act), -0.1, "it logs a firmer plan")
    self.assertTrue(all(a.aLeadProj <= a.aLeadK + 1e-6 for a in act))
    self.assertGreaterEqual(msgs[-1].episodes, 1)
    # first active frame: the shadow's plan is exactly what mode 2 publishes on that frame
    live, _ = drive(planner(lba.MODE_LIVE), frames)
    k = next(i for i, p in enumerate(run(frames)) if p.active)
    first = next(m for m in msgs if m.active)
    self.assertAlmostEqual(first.aTargetAssist, live[k][0], delta=1e-4)
    self.assertEqual(live[:k], base[:k])

  def test_every_frame_that_matters_is_published_and_a_heartbeat_otherwise(self):
    frames = braking(n=100) + [braking(n=100)[-1]] * 60   # the braking, then 3 s of a quiet stopped lead
    pl = planner(lba.MODE_SHADOW)
    flags = []
    pm = _PM()
    for f in frames:
      sm = build_sm(f)
      pl.update(cast(Any, sm))
      lb = pl.lead_brake
      flags.append(bool(lb.proj.active or lb.proj.blocked or lb.proj.reason or lb._ep is not None))
      pl.publish(cast(Any, sm), pm)
    sent = pm.sent['customReserved13']
    self.assertEqual(len(sent), sum(flags) + sum(1 for k, f in enumerate(flags) if not f and k % lba.HEARTBEAT_FRAMES == 0))
    self.assertEqual(sum(m.customReserved13.active for m in sent), sum(p.active for p in run(frames)))
    self.assertLess(len(sent), len(frames))

  def test_live_brakes_sooner(self):
    frames = braking(n=100)
    base, _ = drive(planner(lba.MODE_OFF), frames)
    live, _ = drive(planner(lba.MODE_LIVE), frames)
    tb = next(k for k, o in enumerate(base) if o[0] < -1.0)
    tl = next(k for k, o in enumerate(live) if o[0] < -1.0)
    self.assertLess(tl, tb)
    self.assertLessEqual(min(o[0] for o in live), min(o[0] for o in base) + 1e-6)

  def test_in_live_mode_the_projection_ends_a_reentry_ramp(self):
    # the driver lifts off the gas behind a lead that starts braking hard. radarState's aLeadK is still above the ramp's
    # -2.0 when the projection is already under it: in mode 2 the ramp yields to the projection (the plan is passed as it
    # is), as it yields to a lead radard already sees braking that hard (reentry_ramp.py, round 8 review)
    frames = braking(n=60, brake_at=20, a_brake=-3.0, d0=55.0)   # far enough that the MPC's own plan stays above -2.0
    over = 16
    live = planner(lba.MODE_LIVE)
    lb, ramp = live.lead_brake, live.reentry_ramp
    ends: list = []
    end = ramp._end
    ramp._end = lambda reason: (ends.append((k, reason)) if ramp.active else None, end(reason))[1]
    seen = []
    for k, f in enumerate(frames):
      live.update(cast(Any, build_sm(f, enabled=k >= over, gas=k < over, a_ego=0.3 if k < over else 0.0)))
      seen.append((lb.proj.active, lb.proj.a_lead, f.aLeadK))
    k_proj = next(k for k, (act, a, ak) in enumerate(seen) if act and a < -2.0 <= ak)
    self.assertLess(over, k_proj, "the projection crosses -2.0 inside the re-entry window, radard's aLeadK does not")
    self.assertEqual(ends[0], (k_proj, 'lead'), "the ramp ran until then and ended on the projected lead")

  def test_the_comparison_solve_reproduces_the_planners_own(self):
    # mode 1 with the projection turned into radarState itself: the second MPC must land exactly on the plan
    frames = braking()
    pl = planner(lba.MODE_SHADOW)
    lb = pl.lead_brake
    orig = lb.before_mpc

    def same(radar_state, *a, **k):
      out = orig(radar_state, *a, **k)
      if lb._alt is not None:
        lb._alt = radar_state
      return out
    lb.before_mpc = same
    checked = 0
    for f in frames:
      pl.update(cast(Any, build_sm(f)))
      if lb.compared:
        checked += 1
        self.assertAlmostEqual(lb.a_assist, lb.a_base, delta=1e-5)
        self.assertEqual(lb.fcw_assist, lb.fcw_base)
    self.assertGreater(checked, 5)

  def test_another_layer_changing_the_mpc_candidate_skips_the_comparison(self):
    # close follow (sp-long) blends its own MPC into the plan's trajectory after the solve, below 35 km/h. The second
    # solve cannot reproduce that, so while anything has changed the MPC's candidate the comparison is skipped and
    # flagged - never logged as the assist's effect
    import openpilot.selfdrive.controls.lib.longitudinal_planner as LP
    real = LP.get_accel_from_plan
    frames = braking()
    for shift, want_skip in ((0.0, False), (-0.3, True)):
      pl = planner(lba.MODE_SHADOW)
      with mock.patch.object(LP, 'get_accel_from_plan', side_effect=lambda *a, s=shift, **k: real(*a, **k) + s):
        _, pm = drive(pl, frames, publish=True)
      act = [m.customReserved13 for m in pm.sent['customReserved13'] if m.customReserved13.active]
      self.assertTrue(act)
      self.assertTrue(all(m.skipped == want_skip and m.compared != want_skip for m in act), shift)
      if want_skip:
        self.assertTrue(all(m.aTargetAssist == m.aTargetBase for m in act))

  def test_radarstate_time_not_the_frame_count(self):
    # a plannerd stall: the planner gets every third radarState (the lead easing off at -1.0 m/s^2). Counting planner
    # frames, the speed change over 0.15 s lands in 0.05 s - a -3 m/s^2 slope; radarState's own time keeps it at -1
    frames = braking(n=150, brake_at=10, a_brake=-1.0)
    for f in frames:
      f.aLeadK = min(f.aLeadK, -1.2)   # radard's estimate is not what this tests
    stalled = frames[::3]
    pl = planner(lba.MODE_SHADOW)
    by_count = [pl.lead_brake.assist.update(f, (k + 1) * DT_MDL, V0, model_lead=f.camera) for k, f in enumerate(stalled)]
    self.assertTrue(any(p.reason & lba.REASON_RAW for p in by_count), "the frame clock makes it hard braking")
    pl = planner(lba.MODE_SHADOW)
    for k, f in enumerate(stalled):
      pl.update(cast(Any, build_sm(f, radar_mono=int(1e9 + 3 * k * DT_MDL * 1e9))))
      self.assertFalse(pl.lead_brake.proj.reason & lba.REASON_RAW)
      self.assertGreater(pl.lead_brake.proj.slope, lba.Config().raw_thr)

  def test_every_other_car_stock_acc_and_mode_0_build_nothing(self):
    self.assertIsNone(planner(lba.MODE_LIVE, CAR.HONDA_CIVIC).lead_brake)
    self.assertIsNone(planner(lba.MODE_LIVE, flags=HondaFlagsSP.ELESYS_STOCK_ACC.value).lead_brake)
    self.assertIsNone(planner(lba.MODE_OFF).lead_brake)
    pl = planner(lba.MODE_OFF)
    _, pm = drive(pl, braking(n=30), publish=True)
    self.assertFalse('customReserved13' in pm.sent)

  def test_a_failure_turns_it_off_and_leaves_the_plan_alone(self):
    frames = braking()
    base, _ = drive(planner(lba.MODE_OFF), frames)
    pl = planner(lba.MODE_LIVE)
    with mock.patch.object(lba.LeadBrakeAssist, 'update', side_effect=RuntimeError("boom")):
      out, _ = drive(pl, frames)
    self.assertTrue(pl.lead_brake._failed)
    self.assertEqual(out, base)

  def test_the_second_mpcs_warnings_are_tagged(self):
    # the second MPC's "Long mpc reset" warning must not read as the planner's own MPC failing
    frames = braking()
    pl = planner(lba.MODE_SHADOW)
    ctx = []
    orig = pl.lead_brake._mpc2.update

    def update(*a, **k):
      ctx.append(dict(cloudlog.get_ctx()))
      return orig(*a, **k)
    pl.lead_brake._mpc2.update = update
    drive(pl, frames)
    self.assertTrue(ctx)
    self.assertTrue(all(c.get('leadbrake') == 'second mpc' for c in ctx))
    self.assertFalse('leadbrake' in cloudlog.get_ctx())

  def test_one_log_line_per_episode_with_whether_it_was_engaged(self):
    frames = braking(n=100)
    for enabled in (True, False):
      pl = planner(lba.MODE_SHADOW)
      with mock.patch.object(lba.cloudlog, 'event') as ev:
        drive(pl, frames + [frames[-1]] * 60, enabled=enabled)
      calls = [c for c in ev.call_args_list if c.args and c.args[0] == lba.LOG_TAG]
      self.assertEqual(len(calls), 1, enabled)
      kw = calls[0].kwargs
      self.assertTrue(kw['error'])
      self.assertEqual(kw['mode'], lba.MODE_SHADOW)
      self.assertEqual((kw['engaged'], kw['engaged_any']), (enabled, enabled))
      self.assertEqual((kw['episode'], kw['merged']), (1, 1))
      self.assertTrue(kw['camera'])
      self.assertTrue('duration' in kw)
      if enabled:
        self.assertLess(kw['delta_min'], 0.0)

  def test_episodes_close_together_share_a_line_and_none_is_dropped(self):
    # three short episodes 1 s apart: the first gets its line, the next two wait out LOG_MIN_GAP and share one line
    # (the first version dropped them without a trace)
    pl = lba.LeadBrakePlanner(lba.MODE_SHADOW)
    seq = []
    for start in (10, 30, 47):
      seq += [False] * (start - len(seq)) + [True] * 2
    seq += [False] * 120
    deltas = []
    with mock.patch.object(lba.cloudlog, 'event') as ev:
      for k, act in enumerate(seq):
        pl.t += pl.dt
        pl.proj = lba.Projection(active=act, reason=lba.REASON_RAW if act else 0, track_id=7, a_lead=-3.0, a_lead_k=-1.2)
        pl.a_base, pl.a_assist = -1.0, (-1.5 - 0.1 * k / 50 if act else -1.0)
        if act:
          deltas.append(pl.a_assist - pl.a_base)
        pl._episode(True, 20.0)
    lines = [c.kwargs for c in ev.call_args_list if c.args and c.args[0] == lba.LOG_TAG]
    self.assertEqual([(ln['episode'], ln['merged']) for ln in lines], [(1, 1), (3, 2)])
    self.assertEqual(pl.episodes, 3)
    self.assertEqual(lines[1]['delta_min'], round(min(deltas[2:]), 3))   # the firmest of the two it stands for
    # past LOG_MAX lines, episodes are only counted
    pl._logs = lba.LOG_MAX
    pl._queue(dict(lines[0], episode=4, engaged=True, fcw_assist=False))
    self.assertEqual(pl.unlogged, 1)
