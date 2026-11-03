"""Script-made test clips on Murakami (seeded, so every run keys the same).

Each clip keys the rig's animator controls the way an animator would, to
exercise one part of the animation export (docs/animation-export.md,
section 11). Run on a scene you won't save, or call clear_animation() after.

    import test_animations
    start, end = test_animations.make_clip("fk_body")

Clips (frames 0-48 at the scene rate):

* ``fk_body``     -- every limb in FK; arms, legs, spine, neck and head keyed.
* ``ikfk_switch`` -- arms keyed in IK and FK; the switch goes IK -> FK
                     between frames 20 and 28.
* ``spine``       -- pelvis, spine and chest rotations and translations.
* ``world``       -- the world control (not rebuilt in Unreal) moves and turns,
                     plus a few FK keys.
* ``stress``      -- a 360 degree head spin, a sub-frame key, a non-default
                     rotate order, weighted and stepped tangents, an anim
                     layer, and a keyed face slider.

Only time-based curves are touched: the rig's driven keys stay as they are.

The same clips exist for Murakami V06 (tagged rig) and V010 (built with the
Jeffy toolkit): :data:`CASTS` names, per rig, the controls each clip keys.
"""

import random

import maya.cmds as cmds

import export_animation as ea
import export_rig_manifest as erm
import export_test_poses as etp

CLIPS = ("fk_body", "ikfk_switch", "spine", "world", "stress")
START, END = 0, 48
KEY_FRAMES = (0, 12, 24, 36, 48)
SEED = 11
TEST_LAYER = "kotsudo_test_layer"

# Per rig, the controls the clips key (missing ones are skipped).
CASTS = {
    "v06": {
        "fk_limbs": ["{}_{}FKctrl_0{}".format(s, part, i) for s in "LR" for part in ("Arm", "Leg") for i in (1, 2, 3)],
        "fk_body": ["neck_ctrl_01", "neck_ctrl_03", "head_ctrl", "chest_ctrl", "spine_ctrl_01"],
        "arm_ik": ["L_ArmIKctrl", "R_ArmIKctrl"],
        "spine": ["Pelvis_IKctrl", "spine_ctrl_01", "chest_ctrl"],
        "world": "world_ctrl",
        "world_extra": ["L_ArmFKctrl_01", "R_LegFKctrl_01", "head_ctrl"],
        "head": "head_ctrl",
        "chest": "chest_ctrl",
        "arm_fk": "L_ArmFKctrl_01",
        "slider": ("Head_Settings_Ctrl", "Petal_Rotation"),
        "neck": "neck_ctrl_01",
    },
    "jeffy": {      # Murakami V010
        "fk_limbs": ["{}_{}Leaf{}FK_CTL".format(s, part, i) for s in "LR" for part in ("Arm", "Leg") for i in (1, 2, 3)],
        "fk_body": ["C_Spine6FK_CTL", "C_Spine8FK_CTL", "C_HeadFK_CTL", "C_spineFK3_CTL", "C_spineFK1_CTL"],
        "arm_ik": ["L_ArmLeaf1IK_CTL", "R_ArmLeaf1IK_CTL"],
        "spine": ["C_spineCog_CTL", "C_spineFK2_CTL", "C_spineChestIk_CTL"],
        "world": "C_global_CTL",
        "world_extra": ["L_ArmLeaf1FK_CTL", "R_LegLeaf1FK_CTL", "C_HeadFK_CTL"],
        "head": "C_HeadFK_CTL",
        "chest": "C_spineChestIk_CTL",
        "arm_fk": "L_ArmLeaf1FK_CTL",
        "slider": ("C_Spine5IK3_CTL", "Smile"),
        "neck": "C_Spine6FK_CTL",
    },
}

_ROTATE = ("rotateX", "rotateY", "rotateZ")
_TRANSLATE = ("translateX", "translateY", "translateZ")


def rig_cast():
    """The :data:`CASTS` entry of the open rig."""
    try:
        import jeffy_reader
        if jeffy_reader.is_jeffy_rig():
            return CASTS["jeffy"]
    except ImportError:
        pass
    return CASTS["v06"]


def _controls():
    return etp.manifest_controls(ea.manifest_from_scene())


def _existing(names, controls):
    return [(n, controls[n]) for n in names if n in controls]


def _keyable(node, attributes):
    keyable = set(cmds.listAttr(node, keyable=True, unlocked=True, scalar=True) or [])
    return [a for a in attributes if a in keyable]


def clear_animation():
    """Delete every time-based anim curve and the test layer, and put the
    channels they drove back to their defaults (a deleted curve otherwise
    leaves its channel at whatever frame was last shown). Driven keys
    (animCurveU*, rig behaviour) are kept."""
    if cmds.objExists(TEST_LAYER):
        cmds.delete(TEST_LAYER)
    curves = [c for c in cmds.ls(type="animCurve") or [] if cmds.nodeType(c).startswith("animCurveT")]
    plugs = set()
    for curve in curves:
        plugs.update(cmds.listConnections(curve + ".output", source=False, destination=True,
                                          plugs=True, skipConversionNodes=True) or [])
    if curves:
        cmds.delete(curves)
    for plug in sorted(plugs):
        node, attribute = plug.split(".", 1)
        try:
            default = cmds.attributeQuery(attribute, node=node, listDefault=True)[0]
            cmds.setAttr(plug, default)
        except Exception:
            pass        # a plug that was an anim-layer node, now deleted
    for node, order in list(_CHANGED_ROTATE_ORDERS.items()):
        if cmds.objExists(node):
            cmds.setAttr(node + ".rotateOrder", order)
        del _CHANGED_ROTATE_ORDERS[node]


# Rotate orders the stress clip changed: {node: original order}.
_CHANGED_ROTATE_ORDERS = {}


def _key(node, attribute, frame, value, tangent="auto"):
    cmds.setKeyframe(node, attribute=attribute, time=frame, value=value,
                     inTangentType=tangent, outTangentType=tangent)


def _key_random(rng, node, attributes, spread, frames=KEY_FRAMES, unit=1.0):
    """Key each attribute at ``frames``: its current value + a random offset."""
    for attribute in _keyable(node, attributes):
        base = cmds.getAttr("{}.{}".format(node, attribute))
        for frame in frames:
            _key(node, attribute, frame, base + rng.uniform(-spread, spread) / unit)


def _set_switches(controls, mode, frame=START):
    for switch in etp.switches(ea.manifest_from_scene()):
        _key(switch["node"], switch["attribute"], frame, switch[mode], tangent="linear")


def make_clip(name):
    """Key clip ``name`` on the open Murakami scene. Returns (start, end)."""
    if name not in CLIPS:
        raise ValueError("Unknown clip '{}'; choose from {}.".format(name, ", ".join(CLIPS)))
    clear_animation()
    rng = random.Random("{}:{}".format(SEED, name))
    controls = _controls()
    cast = rig_cast()
    unit = erm._maya_linear_to_centimeters_scale()     # cm per scene unit
    fk_limbs = cast["fk_limbs"]

    if name == "fk_body":
        _set_switches(controls, "fk")
        for _, node in _existing(fk_limbs + cast["fk_body"], controls):
            _key_random(rng, node, _ROTATE, 25.0)

    elif name == "ikfk_switch":
        for switch in etp.switches(ea.manifest_from_scene()):
            if "Arm" not in switch["control"]:
                continue
            _key(switch["node"], switch["attribute"], 0, switch["ik"], tangent="linear")
            _key(switch["node"], switch["attribute"], 20, switch["ik"], tangent="linear")
            _key(switch["node"], switch["attribute"], 28, switch["fk"], tangent="linear")
            _key(switch["node"], switch["attribute"], 48, switch["fk"], tangent="linear")
        for _, node in _existing(cast["arm_ik"], controls):
            _key_random(rng, node, _TRANSLATE, 8.0, unit=unit)
            _key_random(rng, node, _ROTATE, 20.0)
        for _, node in _existing([n for n in fk_limbs if "Arm" in n], controls):
            _key_random(rng, node, _ROTATE, 25.0)

    elif name == "spine":
        _set_switches(controls, "fk")
        for _, node in _existing(cast["spine"], controls):
            _key_random(rng, node, _ROTATE, 20.0)
            _key_random(rng, node, _TRANSLATE, 4.0, unit=unit)

    elif name == "world":
        _set_switches(controls, "fk")
        world = cmds.ls(cast["world"], long=True) or []
        if not world:
            raise RuntimeError("No {} in the scene.".format(cast["world"]))
        _key(world[0], "translateX", 0, 0.0)
        _key(world[0], "translateX", 48, 60.0 / unit)
        _key(world[0], "translateZ", 0, 0.0)
        _key(world[0], "translateZ", 24, 25.0 / unit)
        _key(world[0], "translateZ", 48, 0.0)
        _key(world[0], "rotateY", 0, 0.0)
        _key(world[0], "rotateY", 48, 90.0)
        for _, node in _existing(cast["world_extra"], controls):
            _key_random(rng, node, _ROTATE, 20.0)

    elif name == "stress":
        _set_switches(controls, "fk")
        head = controls.get(cast["head"])
        if head:                                      # a full spin: Euler continuity
            _key(head, "rotateY", 0, 0.0, tangent="linear")
            _key(head, "rotateY", 48, 360.0, tangent="linear")
        chest = controls.get(cast["chest"])
        if chest:                                     # rotate order + sub-frame key
            _CHANGED_ROTATE_ORDERS.setdefault(chest, cmds.getAttr(chest + ".rotateOrder"))
            cmds.setAttr(chest + ".rotateOrder", 2)   # zxy
            _key(chest, "rotateZ", 0, 0.0)
            _key(chest, "rotateZ", 18.5, 15.0)
            _key(chest, "rotateZ", 48, -5.0)
            _key(chest, "rotateX", 0, 0.0)
            _key(chest, "rotateX", 30, 12.0)
            _key(chest, "rotateX", 48, 0.0)
        arm = controls.get(cast["arm_fk"])
        if arm:                                       # weighted, moved tangents
            for frame, value in ((0, 0.0), (24, 40.0), (48, 0.0)):
                _key(arm, "rotateZ", frame, value)
            cmds.keyTangent(arm, attribute="rotateZ", weightedTangents=True)
            cmds.keyTangent(arm, attribute="rotateZ", time=(24, 24), lock=False,
                            inAngle=20.0, outAngle=-35.0, inWeight=6.0, outWeight=14.0)
        slider_name, slider_attr = cast["slider"]
        settings = controls.get(slider_name)
        if settings and slider_attr in _keyable(settings, [slider_attr]):
            _key(settings, slider_attr, 0, 0.0, tangent="step")
            _key(settings, slider_attr, 16, 5.0, tangent="step")
            _key(settings, slider_attr, 32, 2.0, tangent="step")
        neck = controls.get(cast["neck"])
        if neck:                                      # base keys + an additive layer
            _key(neck, "rotateZ", 0, 0.0)
            _key(neck, "rotateZ", 48, 10.0)
            cmds.animLayer(TEST_LAYER)
            cmds.animLayer(TEST_LAYER, edit=True, attribute=neck + ".rotateZ")
            cmds.setKeyframe(neck, attribute="rotateZ", time=24, value=8.0, animLayer=TEST_LAYER)

    cmds.playbackOptions(minTime=START, maxTime=END)
    cmds.currentTime(START, update=True)
    return START, END
