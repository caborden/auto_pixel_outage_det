"""Delayed chip references. Array axes are panel (RMS, mean), then physical chip."""
import math

import numpy as np


def vector(a):
    return np.round(a, 5).tolist()


def group_level(current, allowed):
    return current[:, allowed].mean(axis=1)


def score(current, allowed, chips, stamp, state, args):
    history = [f for f in state.get("history", []) if stamp - f[0] <= 900]
    window = [f for f in history if stamp - f[0] >= 300]
    active = state.get("active", [])
    frozen = state.get("frozen", {})
    held = state.get("reconfig_anchor")
    result = dict(stamp=stamp, active=active, events=[], window_frames=len(window),
                  excluded=int((~allowed).sum()))
    extra = dict(history=history, frozen=frozen, last_good=state.get("last_good"),
                 reconfig_anchor=held, recovered=0)
    if not allowed.any():
        raise ValueError("no eligible chips in group")
    # Use whole-group mean-panel coverage and require a coherent change on several tiles.
    tile_levels = np.array([np.mean(current[1, allowed & (chips[:, 0] == tile)])
                            if np.any(allowed & (chips[:, 0] == tile)) else 0
                            for tile in range(1, 9)])
    levels = np.r_[group_level(current, allowed)[1], tile_levels]
    # A short median also catches a transition spread over several updates.
    recent = [f for f in history if stamp - f[0] <= 120]
    anchor = held
    if anchor is None and len(recent) >= 3:
        anchor = np.median([f[2] for f in recent], axis=0)
    if anchor is None and stamp - state.get("stamp", stamp) <= args.max_gap:
        anchor = extra["last_good"]
    if anchor is not None:
        ratios = levels / np.maximum(anchor, .01)
        result["iog_mean_ratio"] = round(float(ratios[0]), 4)
        if held is None and ratios[0] <= args.reconfig_ratio and (ratios[1:] <= .85).sum() >= 4:
            held = vector(anchor)
        if held is not None:
            restored = ratios[0] >= .85 and (ratios[1:] >= .80).sum() >= 5
            extra["recovered"] = (state.get("recovered", 0) + 1 if restored else 0)
            extra["reconfig_anchor"] = held if extra["recovered"] < 2 else None
            result["status"] = "likely_reconfiguration" if extra["reconfig_anchor"] else "reconfiguration_cleared"
            return result, extra, None
    extra["last_good"] = vector(levels)
    sample = current.copy()
    for key, ref in frozen.items():
        sample[:, int(key)] = ref
    if len(window) < 3:
        history.append([stamp, vector(sample), vector(levels)])
        result["status"] = "warming_up"
        return result, extra, None
    baseline = np.median([f[1] for f in window], axis=0)
    for key, ref in frozen.items():
        baseline[:, int(key)] = ref
    eligible = allowed & (baseline.min(axis=0) >= args.min_coverage)
    raw = current / np.maximum(baseline, .001)
    hits = set()
    diagnostics = []
    broad = []
    thin = []
    for tile in range(1, 9):
        ii = np.flatnonzero(eligible & (chips[:, 0] == tile))
        if len(ii) < 10:
            thin.append(tile)
            continue
        local = raw[:, ii]
        # Estimate peer activity without the strongest preliminary local losses.
        preliminary = (local.min(axis=0) <= args.ratio) & (local.max(axis=0) <= args.secondary_ratio)
        peer = np.median(local, axis=1)
        if peer.min() <= .50 and peer.max() <= .80:
            others = eligible & (chips[:, 0] != tile)
            broad.append(dict(tile=tile, retained=vector(peer),
                              other_tiles_retained=vector(np.median(raw[:, others], axis=1))
                              if others.any() else None))
            suspect = np.ones(len(ii), dtype=bool)
        else:
            peers = local[:, ~preliminary]
            if peers.shape[1] >= max(10, math.ceil(len(ii)/2)):
                peer = np.median(peers, axis=1)
            normalized = local / np.maximum(peer[:, None], .05)
            suspect = (normalized.min(axis=0) <= args.ratio) & (normalized.max(axis=0) <= args.secondary_ratio)
            for j in np.flatnonzero(suspect):
                i = int(ii[j])
                hits.add(i)
                diagnostics.append(dict(tile=tile, chip=int(chips[i, 1]), current=vector(current[:, i]),
                                        reference=vector(baseline[:, i]), peer=vector(peer),
                                        adjusted=vector(normalized[:, j])))
        for j, index in enumerate(ii):
            key = str(index)
            if suspect[j]:
                frozen[key] = vector(baseline[:, index])
            elif index not in state.get("hits", []):
                frozen.pop(key, None)
    # A high-activity frame should not raise the future reference for a tile.
    for tile in range(1, 9):
        sel = eligible & (chips[:, 0] == tile)
        if sel.any() and np.median(raw[:, sel], axis=1).max() > 1.5:
            sample[:, sel] = baseline[:, sel]
    for key, ref in frozen.items():
        sample[:, int(key)] = ref
    history.append([stamp, vector(sample), vector(levels)])
    result.update(status="evaluated", broad_tile_losses=broad, insufficient_tiles=thin,
                  candidates=diagnostics, previous_stamp=state.get("stamp"))
    return result, extra, hits
