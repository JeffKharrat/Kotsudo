"""Animation clip export (Maya side), the shared start of both animation axes.

One clip writes two files next to the rig FBX (see docs/animation-export.md):

* ``<base>.<clip>.anim.fbx`` -- the joints (and meshes) baked on every frame.
  Unreal imports it as an Anim Sequence: the exact answer key, and the clip
  axis 1 retakes on.
* ``<base>.<clip>.anim.json`` -- the sidecar:
    - per frame: every animator control's world state and channel values,
      every exported joint's world state, every blendshape weight
      (Unreal axes, cm, quaternions -- the pose-check conventions);
    - per channel: the animator's own keys (times, values, tangent types and
      slopes, weights, infinity) when the channel is driven by one
      time-based anim curve -- what axis 2 copies onto the Control Rig;
    - notes: channels that are not a single curve (anim layers, constraints,
      pair blends) and controls whose Maya parents move but are not rebuilt
      in Unreal (e.g. an animated world_ctrl).

``<base>`` is the rig's file name (the manifest's ``poses_file`` without
``.poses.json``), so Unreal finds the clip next to the rig.

Usage in Maya (rig scene with keys):

    import export_animation
    export_animation.export_clip_from_scene("walk")              # timeline range
    export_animation.export_clip_from_scene("walk", 10, 58)      # explicit range

Driven keys (``animCurveU*``) are rig behaviour, rebuilt by the rig builder;
only time-based curves (``animCurveT*``) are animation.
"""

import datetime
import json
import math
import os
import re

import maya.cmds as cmds

import export_rig_manifest as erm
import export_test_poses as etp

ANIM_SCHEMA_VERSION = 1

_TRANSLATE = ("translateX", "translateY", "translateZ")
_ROTATE = ("rotateX", "rotateY", "rotateZ")
_SCALE = ("scaleX", "scaleY", "scaleZ")

# Maya time units -> frames per second.
_TIME_UNIT_FPS = {
    "game": 15.0, "film": 24.0, "pal": 25.0, "ntsc": 30.0, "show": 48.0,
    "palf": 50.0, "ntscf": 60.0, "sec": 1.0, "millisec": 1000.0,
    "min": 1.0 / 60.0, "hour": 1.0 / 3600.0,
}
_INFINITY = {0: "constant", 1: "linear", 2: "constant", 3: "cycle",
             4: "cycle_offset", 5: "oscillate"}
_CONSTRAINT_TYPES = ("parentConstraint", "pointConstraint", "orientConstraint",
                     "scaleConstraint", "aimConstraint", "poleVectorConstraint")
# Half a thousandth of a frame: small enough that a curve is locally straight,
# large enough to stay well above double rounding.
_SLOPE_STEP = 5e-4


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def scene_fps():
    """(frames per second, Maya time unit name) of the scene."""
    unit = str(cmds.currentUnit(query=True, time=True))
    if unit in _TIME_UNIT_FPS:
        return _TIME_UNIT_FPS[unit], unit
    match = re.match(r"^([0-9.]+)fps$", unit)
    if match:
        return float(match.group(1)), unit
    raise RuntimeError("Unknown Maya time unit '{}'.".format(unit))


def playback_range():
    """(start, end) frames of the timeline's playback range."""
    return (cmds.playbackOptions(query=True, minTime=True),
            cmds.playbackOptions(query=True, maxTime=True))


def clip_frames(start, end, step=1.0):
    """Frames sampled for a clip: start, start + step, ... up to end."""
    count = int(math.floor((end - start) / step + 1e-6)) + 1
    return [round(start + i * step, 6) for i in range(count)]


# ---------------------------------------------------------------------------
# Channels: what drives them, and their keys
# ---------------------------------------------------------------------------

def _angle_to_degrees():
    unit = str(cmds.currentUnit(query=True, angle=True)).lower()
    return 180.0 / math.pi if unit.startswith("rad") else 1.0


def channel_value_scale(node, attribute):
    """Factor from a channel's UI value to the sidecar's unit:
    cm for distances, degrees for angles, 1 for plain numbers."""
    kind = cmds.getAttr("{}.{}".format(node, attribute), type=True)
    if kind == "doubleLinear":
        return erm._maya_linear_to_centimeters_scale()
    if kind == "doubleAngle":
        return _angle_to_degrees()
    return 1.0


def channel_source(node, attribute):
    """(kind, node) of what drives a channel.

    kind: "keys" (one time-based anim curve: the animator's keys), "static"
    (nothing), "driven_keys" (rig behaviour), "layered" (anim layer blend),
    "constrained", "blended" (pairBlend), or "connected" (anything else).
    """
    plug = "{}.{}".format(node, attribute)
    sources = cmds.listConnections(plug, source=True, destination=False,
                                   skipConversionNodes=True) or []
    if not sources:
        return "static", None
    source = sources[0]
    kind = cmds.nodeType(source)
    if kind.startswith("animCurveT"):
        return "keys", source
    if kind.startswith("animCurveU"):
        return "driven_keys", source
    if kind.startswith("animBlendNode"):
        return "layered", source
    if kind in _CONSTRAINT_TYPES:
        return "constrained", source
    if kind == "pairBlend":
        return "blended", source
    return "connected", source


def _curve_value(curve, frame):
    return cmds.keyframe(curve, query=True, eval=True, time=(frame, frame))[0]


def _slopes(curve, frame):
    """(in slope, out slope) at a key, per frame, in the curve's UI unit.

    Measured on the curve Maya evaluates, one-sided, with a Richardson step
    so the result is second-order accurate. This is the same number whatever
    the tangent type (auto, spline, clamped, flat, linear, fixed), which is
    what Unreal needs to rebuild the curve.
    """
    h = _SLOPE_STEP
    v = _curve_value(curve, frame)
    back1, back2 = _curve_value(curve, frame - h), _curve_value(curve, frame - h / 2.0)
    fwd1, fwd2 = _curve_value(curve, frame + h), _curve_value(curve, frame + h / 2.0)
    slope_in = 2.0 * (v - back2) / (h / 2.0) - (v - back1) / h
    slope_out = 2.0 * (fwd2 - v) / (h / 2.0) - (fwd1 - v) / h
    return slope_in, slope_out


def read_curve(curve, value_scale=1.0):
    """The keys of a time-based anim curve, in the sidecar's units.

    Each key: [frame, value, in_type, out_type, in_slope, out_slope,
    in_weight, out_weight]. Slopes are value units per frame, measured on
    the evaluated curve (see _slopes). Maya's raw tangent angles and the
    lock flags are kept beside them for reference.
    """
    times = cmds.keyframe(curve, query=True, timeChange=True) or []
    values = cmds.keyframe(curve, query=True, valueChange=True) or []
    in_types = cmds.keyTangent(curve, query=True, inTangentType=True) or []
    out_types = cmds.keyTangent(curve, query=True, outTangentType=True) or []
    in_weights = cmds.keyTangent(curve, query=True, inWeight=True) or []
    out_weights = cmds.keyTangent(curve, query=True, outWeight=True) or []
    in_angles = cmds.keyTangent(curve, query=True, inAngle=True) or []
    out_angles = cmds.keyTangent(curve, query=True, outAngle=True) or []
    locks = cmds.keyTangent(curve, query=True, lock=True) or []
    weighted = bool((cmds.keyTangent(curve, query=True, weightedTangents=True) or [False])[0])

    keys = []
    for i, frame in enumerate(times):
        slope_in, slope_out = _slopes(curve, frame)
        out_type = out_types[i] if i < len(out_types) else "auto"
        if out_type in ("step", "stepnext"):
            slope_out = 0.0
        keys.append([
            round(frame, 6),
            round(values[i] * value_scale, 6),
            in_types[i] if i < len(in_types) else "auto",
            out_type,
            round(slope_in * value_scale, 6),
            round(slope_out * value_scale, 6),
            round(in_weights[i], 6) if i < len(in_weights) else 1.0,
            round(out_weights[i], 6) if i < len(out_weights) else 1.0,
        ])
    return {
        "curve": curve,
        "type": cmds.nodeType(curve),
        "weighted": weighted,
        "infinity": [_INFINITY.get(cmds.getAttr(curve + ".preInfinity"), "constant"),
                     _INFINITY.get(cmds.getAttr(curve + ".postInfinity"), "constant")],
        "key_fields": ["frame", "value", "in_type", "out_type", "in_slope", "out_slope",
                       "in_weight", "out_weight"],
        "keys": keys,
        "maya_angles": [[round(a, 6), round(b, 6)] for a, b in zip(in_angles, out_angles)],
        "locked": [bool(x) for x in locks],
    }


def control_channels(node, manifest, control_name):
    """Keyable, unlocked channels of a control: transform channels, then the
    custom attributes the manifest exports for it."""
    keyable = set(cmds.listAttr(node, keyable=True, unlocked=True, scalar=True) or [])
    transform = [c for c in _TRANSLATE + _ROTATE + _SCALE if c in keyable]
    custom = [a for a in etp._attribute_names(manifest, control_name) if a in keyable]
    return transform + custom


def _animated_ancestors(node, control_nodes):
    """Transforms above ``node`` (long names) that carry time-based keys (or
    a layer) and are not themselves exported controls: Unreal won't rebuild
    their motion from keys, only from the per-frame world samples."""
    found = []
    parent = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
    while parent:
        if parent not in control_nodes:
            for attribute in _TRANSLATE + _ROTATE + _SCALE:
                if not cmds.attributeQuery(attribute, node=parent, exists=True):
                    continue
                if channel_source(parent, attribute)[0] in ("keys", "layered"):
                    found.append(parent)
                    break
        parent = (cmds.listRelatives(parent, parent=True, fullPath=True) or [None])[0]
    return found


# ---------------------------------------------------------------------------
# Rest state (shared with the pose check)
# ---------------------------------------------------------------------------

def _default(node, attribute):
    try:
        return cmds.attributeQuery(attribute, node=node, listDefault=True)[0]
    except Exception:
        return None


def control_rest(controls, manifest, extra_nodes=()):
    """{name: {t, q, o}} of every control with its channels at their defaults.

    The same rest the pose check records, so Unreal replays clip frames with
    the same "Unreal rest x Maya world delta" rule. ``extra_nodes`` (animated
    parents that are not controls, e.g. world_ctrl) get their transform
    channels reset too. Taken without changing time: keyed channels are set,
    captured, and restored.
    """
    touched = []
    to_reset = [(node, control_channels(node, manifest, name)) for name, node in controls.items()]
    for node in extra_nodes:
        keyable = set(cmds.listAttr(node, keyable=True, unlocked=True, scalar=True) or [])
        to_reset.append((node, [c for c in _TRANSLATE + _ROTATE + _SCALE if c in keyable]))
    try:
        for node, attributes in to_reset:
            for attribute in attributes:
                default = _default(node, attribute)
                if default is None:
                    continue
                plug = "{}.{}".format(node, attribute)
                previous = cmds.getAttr(plug)
                try:
                    cmds.setAttr(plug, default)
                except Exception:
                    continue
                touched.append((plug, previous))
        rest = {}
        for name, node in controls.items():
            position, rotation = etp.world_state(node)
            origin = erm._controller_origin_world(node)[0]
            rest[name] = {
                "t": position, "q": rotation,
                "o": [round(c, 4) for c in erm._maya_vector_to_unreal(origin, apply_unit_scale=True)],
            }
        return rest
    finally:
        for plug, previous in reversed(touched):
            try:
                cmds.setAttr(plug, previous)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def sanitize_clip_name(name):
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "").strip()).strip("_")
    if not clean:
        raise ValueError("A clip needs a name (letters, digits, _ or -).")
    return clean


def rig_base_name(manifest):
    """The rig FBX's file name without extension (from the manifest)."""
    poses_file = manifest.get("poses_file") or ""
    if poses_file.endswith(".poses.json"):
        return poses_file[:-len(".poses.json")]
    return manifest.get("rig_name") or "rig"


def manifest_from_scene():
    plug = "{}.{}".format(erm.ROOT_JOINT_NAME, erm.MANIFEST_ATTR)
    if not cmds.objExists(plug):
        raise RuntimeError("No manifest on '{}': export the rig first.".format(plug))
    return json.loads(cmds.getAttr(plug))


def default_export_dir():
    """The repo's FBXs folder, where Unreal looks for rig files."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo, "FBXs")


def scan_animation(manifest, controls=None, read_keys=False):
    """What drives each control channel, before any sampling.

    Returns {"channels": {control: [(attribute, scale, record)]},
    "parents_animated": {control: [long names]}, "animated_parents": [long
    names], "notes": [...]}. ``record`` is {"source": kind, ...}, plus the
    keys (read_curve) when ``read_keys``.
    """
    controls = controls if controls is not None else etp.manifest_controls(manifest)
    control_nodes = set(controls.values())
    channel_plan, notes = {}, []
    for name, node in sorted(controls.items()):
        plan = []
        for attribute in control_channels(node, manifest, name):
            scale = channel_value_scale(node, attribute)
            kind, source = channel_source(node, attribute)
            record = {"source": kind}
            if kind == "keys":
                if read_keys:
                    record.update(read_curve(source, scale))
                else:
                    record["curve"] = source
            elif kind != "static":
                record["driver"] = source
                if kind in ("layered", "constrained", "blended", "connected"):
                    notes.append("{}.{}: {} ({}), per-frame values only".format(
                        name, attribute, kind, source))
            plan.append((attribute, scale, record))
        channel_plan[name] = plan

    parents_animated = {}
    for name, node in sorted(controls.items()):
        animated = _animated_ancestors(node, control_nodes)
        if animated:
            parents_animated[name] = animated
    animated_parents = sorted({p for found in parents_animated.values() for p in found})
    for parent in animated_parents:
        moved = sorted(n for n, found in parents_animated.items() if parent in found)
        notes.append("{} is animated but not an exported control; it moves {} control(s) "
                     "(e.g. {}). Their keys alone miss that motion.".format(
                         erm._short_node_name(parent), len(moved), ", ".join(moved[:4])))
    return {"channels": channel_plan, "parents_animated": parents_animated,
            "animated_parents": animated_parents, "notes": notes}


def build_clip(manifest, clip_name, start, end, step=1.0):
    """The sidecar document for frames start..end. Restores the current time."""
    fps, maya_unit = scene_fps()
    frames = clip_frames(start, end, step)
    joints = erm._exported_joints()
    controls = etp.manifest_controls(manifest)
    found_switches = etp.switches(manifest)
    blendshape_plugs = [
        (entry["morph"], "{}.{}".format(entry["blendshape"], entry["target"]))
        for entry in (manifest.get("blendshapes") or [])
        if entry.get("morph") and cmds.objExists("{}.{}".format(entry["blendshape"], entry["target"]))
    ]

    # Channels and their sources, read once (they don't change over time).
    scan = scan_animation(manifest, controls, read_keys=True)
    channel_plan, notes = scan["channels"], scan["notes"]
    parents_animated = scan["parents_animated"]
    rest = control_rest(controls, manifest, scan["animated_parents"])

    samples = {name: [] for name in controls}
    values = {name: {attribute: [] for attribute, _, _ in channel_plan[name]} for name in controls}
    joint_samples = {erm._short_node_name(j): [] for j in joints}
    blendshape_samples = {morph: [] for morph, _ in blendshape_plugs}
    switch_samples = {"{}.{}".format(s["control"], s["attribute"]): [] for s in found_switches}

    current = cmds.currentTime(query=True)
    cmds.refresh(suspend=True)
    try:
        for frame in frames:
            cmds.currentTime(frame, update=True)
            for name, node in controls.items():
                position, rotation = etp.world_state(node)
                samples[name].append([position, rotation])
                for attribute, scale, _ in channel_plan[name]:
                    value = cmds.getAttr("{}.{}".format(node, attribute))
                    values[name][attribute].append(round(float(value) * scale, 6))
            for joint in joints:
                position, rotation = etp.world_state(joint)
                joint_samples[erm._short_node_name(joint)].append([position, rotation])
            for morph, plug in blendshape_plugs:
                blendshape_samples[morph].append(round(float(cmds.getAttr(plug)), 6))
            for s in found_switches:
                switch_samples["{}.{}".format(s["control"], s["attribute"])].append(
                    float(cmds.getAttr("{}.{}".format(s["node"], s["attribute"]))))
    finally:
        cmds.currentTime(current, update=True)
        cmds.refresh(suspend=False)

    control_records = {}
    for name, node in sorted(controls.items()):
        channels = {}
        for attribute, _, record in channel_plan[name]:
            record = dict(record)
            record["values"] = values[name][attribute]
            channels[attribute] = record
        control_records[name] = {
            "dag_path": node,
            "rotate_order": int(cmds.getAttr(node + ".rotateOrder")),
            "samples": samples[name],
            "channels": channels,
        }
        if name in parents_animated:
            control_records[name]["parents_animated"] = [erm._short_node_name(p) for p in parents_animated[name]]

    return {
        "schema": "kotsudo.anim",
        "schema_version": ANIM_SCHEMA_VERSION,
        "rig_name": manifest.get("rig_name"),
        "manifest_schema_version": manifest.get("schema_version"),
        "clip": clip_name,
        "source": {
            "scene": cmds.file(query=True, sceneName=True) or None,
            "maya_version": cmds.about(version=True),
            "exported": datetime.datetime.now().isoformat(timespec="seconds"),
        },
        "time": {"fps": fps, "maya_unit": maya_unit, "start": frames[0], "end": frames[-1],
                 "step": step, "frames": frames},
        "units": {"linear": "cm", "rotation": "quat_xyzw_world",
                  "channel_linear": "cm", "channel_angle": "deg", "slope": "value_per_frame"},
        "control_rest": rest,
        "switches": [{k: s[k] for k in ("module", "control", "attribute", "ik", "fk")}
                     for s in found_switches],
        "switch_values": switch_samples,
        "controls": control_records,
        "blendshapes": blendshape_samples,
        "joints": joint_samples,
        "notes": notes,
    }


# Summary of the last export_clip(), for the tool.
LAST_CLIP_SUMMARY = {}


def export_clip(manifest, export_dir, clip_name, start=None, end=None, bake_fbx=True):
    """Write ``<base>.<clip>.anim.json`` (and the baked ``.anim.fbx``).

    Returns the summary dict (paths, frames, keyed channels, notes).
    """
    clip_name = sanitize_clip_name(clip_name)
    if start is None or end is None:
        range_start, range_end = playback_range()
        start = range_start if start is None else start
        end = range_end if end is None else end
    if end < start:
        raise ValueError("The clip ends ({}) before it starts ({}).".format(end, start))
    if not os.path.isdir(export_dir):
        os.makedirs(export_dir)
    base = "{}.{}.anim".format(rig_base_name(manifest), clip_name)
    json_path = os.path.join(export_dir, base + ".json")
    fbx_path = os.path.join(export_dir, base + ".fbx")

    document = build_clip(manifest, clip_name, start, end)
    if bake_fbx:
        erm.export_skeleton_and_meshes_fbx(fbx_path, bake_range=(start, end))
        erm.verify_exported_fbx(fbx_path, manifest)
        document["fbx_file"] = os.path.basename(fbx_path)
    with open(json_path, "w") as handle:
        json.dump(document, handle, separators=(",", ":"))

    keyed = [(name, attribute, len(record["keys"]))
             for name, control in document["controls"].items()
             for attribute, record in control["channels"].items() if record["source"] == "keys"]
    LAST_CLIP_SUMMARY.clear()
    LAST_CLIP_SUMMARY.update({
        "json": json_path,
        "fbx": fbx_path if bake_fbx else None,
        "frames": len(document["time"]["frames"]),
        "fps": document["time"]["fps"],
        "range": (document["time"]["start"], document["time"]["end"]),
        "controls": len(document["controls"]),
        "keyed_controls": len({name for name, _, _ in keyed}),
        "keyed_channels": len(keyed),
        "keys": sum(count for _, _, count in keyed),
        "notes": list(document["notes"]),
    })
    print("[AnimExport] Clip '{}': frames {}-{} at {:g} fps, {} keyed channel(s) on {} control(s) "
          "-> {}".format(clip_name, document["time"]["start"], document["time"]["end"],
                         document["time"]["fps"], len(keyed), LAST_CLIP_SUMMARY["keyed_controls"],
                         json_path))
    for note in document["notes"]:
        print("[AnimExport]   note: " + note)
    return dict(LAST_CLIP_SUMMARY)


def export_clip_from_scene(clip_name, start=None, end=None, export_dir=None, bake_fbx=True):
    """Export a clip with the manifest last written on the root joint."""
    return export_clip(manifest_from_scene(), export_dir or default_export_dir(),
                       clip_name, start, end, bake_fbx)
