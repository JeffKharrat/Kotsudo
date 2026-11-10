"""Jeffy rig reader (J0), checked on Murakami V010 (run with mayapy on a scene copy).

    "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya_jeffy_reader_test.py <V010_copy.ma> [out_dir]

Checks, on a rig built with the Jeffy toolkit (docs/jeffy-v010-support.md):

  * every Jeffy setup is read (types, controls, outputs), and the skeleton
    root is the one the rig_base setup declares (``Root``);
  * every skinned joint belongs to exactly one module;
  * the tool lists the setups as modules (orange: not rebuilt in Unreal yet);
  * a full Kotsudo export works: FBX verified (the meshes named like joints
    are renamed for the export), manifest valid, with a ``jeffy`` section
    whose control records the pose check finds; test poses written;
  * an animation clip on Jeffy controls exports, and its samples equal a
    fresh capture.

The scene is never saved.
"""
import collections
import json
import math
import os
import random
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone  # noqa: E402

maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402
from maya.api import OpenMaya as om  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "maya"))
sys.path.insert(0, os.path.join(HERE, ".."))
import export_animation as ea  # noqa: E402
import export_rig_manifest as erm  # noqa: E402
import export_test_poses as etp  # noqa: E402
import jeffy_reader  # noqa: E402
import rig_tagger_tool  # noqa: E402

FAILURES = []
EXPECTED_TYPES = {"fk_chain": 20, "ikfk_limb": 4, "ribbon": 4, "rig_base": 1, "spline": 2, "wire_attribute": 4}


def check(condition, message):
    if not condition:
        FAILURES.append(message)
        print("  FAIL " + message)
    return condition


def q_angle(a, b):
    dot = min(1.0, abs(sum(x * y for x, y in zip(a, b))) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))))
    return math.degrees(2.0 * math.acos(dot))


def apply_ops(value, ops):
    """The exporter's driver maths, in python (the ops J2 uses)."""
    for op in ops or []:
        kind = op["op"]
        if kind == "mul":
            value *= op["value"]
        elif kind == "add":
            value += op["value"]
        elif kind == "remap":
            (a, b), (c, d) = op["from"], op["to"]
            t = min(1.0, max(0.0, (value - a) / (b - a))) if b != a else 0.0
            value = c + t * (d - c)
        elif kind == "compare":
            value = op["true"] if abs(value - op["value"]) < 1e-9 else op["false"]
        else:
            raise ValueError(kind)
    return value


def check_j2(setups, manifest):
    """J2: the fk_chain / rig_base controls as standard Kotsudo records
    (parents, space switches, driven groups) and the face-slider wires as
    blendshape drivers -- what the Unreal builder rebuilds."""
    from rig_builder import driven_groups as dg
    from rig_builder import manifest_schema

    print("\n-- J2 records --")
    described = [s for s in setups if s["type"] in jeffy_reader.DESCRIBED_TYPES]
    records = {r["name"]: r for rs in (manifest.get("bone_controllers") or {}).values() for r in rs}
    expected = sum(len(s["controls"]) for s in described)
    print("records: {} (expected {})".format(len(records), expected))
    check(len(records) == expected, "bone_controllers holds {} records, expected {}".format(len(records), expected))

    errors, warnings = manifest_schema.validate_manifest(manifest)
    print("manifest validation: {} error(s), {} warning(s)".format(len(errors), len(warnings)))
    check(not errors, "manifest errors: {}".format(errors[:3]))

    # FK chains: the top control switches space, the others hang from the previous control.
    fk = [s for s in setups if s["type"] == "fk_chain"]
    blends = 0
    for setup in fk:
        names = [c["name"] for c in setup["controls"]]
        top = records.get(names[0]) or {}
        blend = top.get("parent_space_blend") or {}
        if check(blend, "{}: no space switch".format(names[0])):
            blends += 1
            targets = blend["targets"]
            check(all(t.get("solo") for t in targets) and len(blend["switch"]["labels"]) == len(targets),
                  "{}: space targets incomplete".format(names[0]))
            check(blend["switch"]["labels"][0] == "parent", "{}: first space {}".format(
                names[0], blend["switch"]["labels"][0]))
        check(top.get("role") == "bone_driver", "{}: role {}".format(names[0], top.get("role")))
        for previous, name in zip(names, names[1:]):
            check((records.get(name) or {}).get("parent_controllers") == [previous],
                  "{}: parents {}".format(name, (records.get(name) or {}).get("parent_controllers")))
    print("fk chains: {}, space switches: {}".format(len(fk), blends))

    # rig_base: the root control drives the root joint, under the global control.
    base = next(s for s in setups if s["type"] == "rig_base")
    by_key = {c["key"]: records.get(c["name"]) or {} for c in base["controls"]}
    check(by_key["root"].get("role") == "bone_driver" and str(by_key["root"].get("driven_bone")).endswith("Root"),
          "rig_base root control: {} {}".format(by_key["root"].get("role"), by_key["root"].get("driven_bone")))
    check(by_key["root"].get("parent_controllers") == [base["controls"][0]["name"]],
          "rig_base root parents {}".format(by_key["root"].get("parent_controllers")))

    # length: a driven group on the next control; the Unreal maths vs Maya.
    driven = [(r, g) for r in records.values() for g in r.get("offset_stack") or [] if g.get("kind") == "driven"]
    print("driven groups (length):", len(driven))
    # one `length` group per FK chain of two or more controls, two per limb (fk1, fk2)
    expected_driven = sum(1 for s in fk if len(s["controls"]) > 1) + 2 * sum(1 for s in setups if s["type"] == "ikfk_limb")
    check(len(driven) == expected_driven, "driven groups: {} (expected {})".format(len(driven), expected_driven))
    worst_p = worst_r = 0.0
    for record, group in driven[:4]:
        node = cmds.ls(group["name"], long=True)[0]
        driver = next(iter(group["driven"].values()))
        plug = "{}.{}".format(driver["control"], driver["attr"])
        previous = cmds.getAttr(plug)
        for value in (0.4, 1.7):
            cmds.setAttr(plug, value)
            values = dict(group["rest"])
            for channel, d in group["driven"].items():
                values[channel] = apply_ops(value, d["ops"])
            predicted = dg.compose(dg.compose(dg.local_unreal(group, values),
                                              dg.inverse(dg.local_unreal(group, group["rest"]))),
                                   dg.pose_to_transform(group["rest_world"]))
            actual = dg.pose_to_transform(erm._world_pose_unreal(node))
            worst_p = max(worst_p, math.dist(predicted[1], actual[1]))
            worst_r = max(worst_r, q_angle(predicted[0], actual[0]))
        cmds.setAttr(plug, previous)
    print("driven group maths vs Maya: {:.6f} cm, {:.5f} deg".format(worst_p, worst_r))
    check(worst_p < 1e-3 and worst_r < 1e-3, "driven group maths off ({:.4f} cm)".format(worst_p))

    # Face sliders: the wires as blendshape drivers, their control as a settings control.
    wires = [s for s in setups if s["type"] == "wire_attribute"]
    entries = {e["target"]: e for e in manifest.get("blendshapes") or []}
    for wire in wires:
        target = str(wire["slots"]["targets"][0]).split(".")[-1]
        entry = entries.get(target) or {}
        source = str(wire["slots"]["source"])
        ok = (entry.get("driver") or {}).get("control") == source.split(".")[0] and not entry.get("unsupported")
        check(ok, "wire {} -> {}: {}".format(source, target, entry.get("unsupported") or entry.get("driver")))
        if ok:
            control, attribute = source.split(".")
            for value in (0.0, 2.5, 7.5, 10.0):         # the slider's range (0-10)
                cmds.setAttr(source, value)
                weight = cmds.getAttr(wire["slots"]["targets"][0])
                predicted = apply_ops(value, entry["driver"].get("ops") or entry.get("ops") or [])
                check(abs(weight - predicted) < 1e-4, "wire {} at {}: Maya {} vs {}".format(
                    target, value, weight, predicted))
            cmds.setAttr(source, 0.0)
    settings = [r["name"] for r in manifest.get("settings_controls") or []]
    print("blendshape drivers: {}, settings controls: {}".format(len(entries), settings))
    check(len(entries) >= len(wires), "blendshape entries {}".format(len(entries)))


# ---------------------------------------------------------------------------
# J3-J5: the maths the Unreal modules compute, replayed in python against Maya
# ---------------------------------------------------------------------------

def _m(node):
    found = cmds.ls(node, type="joint", long=True) or cmds.ls(node, long=True)
    return om.MMatrix(cmds.xform(found[0] if found else node, q=True, ws=True, m=True))


def _t(matrix):
    return [matrix.getElement(3, i) for i in range(3)]


def _q(matrix):
    return om.MTransformationMatrix(matrix).rotation(asQuaternion=True)


def _from_qt(q, t):
    tm = om.MTransformationMatrix()
    tm.setRotation(q)
    tm.setTranslation(om.MVector(*t), om.MSpace.kWorld)
    return tm.asMatrix()


def _axes(matrix):
    return [om.MVector(matrix.getElement(r, 0), matrix.getElement(r, 1), matrix.getElement(r, 2)) for r in range(3)]


def _frame(position, x_axis, y_axis, z_axis):
    m = om.MMatrix()
    for r, axis in enumerate((x_axis, y_axis, z_axis)):
        for c in range(3):
            m.setElement(r, c, axis[c])
    for c in range(3):
        m.setElement(3, c, position[c])
    return m


def _rot_err(a, b):
    qa, qb = _q(a), _q(b)
    return math.degrees(2 * math.acos(min(1.0, abs(qa.x * qb.x + qa.y * qb.y + qa.z * qb.z + qa.w * qb.w))))


def _maya_axis(ue_axis):
    """Unreal local axis back to Maya axes (X, Z, Y swap)."""
    return om.MVector(ue_axis[0], ue_axis[2], ue_axis[1])


def _aim_matrix(input_matrix, aim_axis, target_point, up_axis, up_direction):
    """What aimMatrix (aim + align) and Control Rig's Aim Math compute."""
    position = om.MVector(*_t(input_matrix))
    forward = (om.MVector(*target_point) - position).normal()
    up = om.MVector(*up_direction)
    up = (up - forward * (up * forward)).normal()
    third = forward ^ up
    # the frame whose local aim_axis is forward and local up_axis is up
    local = _frame((0, 0, 0), aim_axis, up_axis, aim_axis ^ up_axis)
    world = _frame((0, 0, 0), forward, up, third)
    rotation = local.inverse() * world
    for c in range(3):
        rotation.setElement(3, c, position[c])
    return rotation


def _eval_expr(expr):
    kind = expr["kind"]
    if kind == "bone":
        return _m(expr["joint"])
    if kind == "control":
        return _m(expr["name"])
    if kind == "lerp":
        a, b = _eval_expr(expr["a"]), _eval_expr(expr["b"])
        t = expr.get("t", 0.5)
        ta, tb = om.MVector(*_t(a)), om.MVector(*_t(b))
        translation = ta + (tb - ta) * (t * expr.get("translate", 1.0))
        rotation = om.MQuaternion.slerp(_q(a), _q(b), t * expr.get("rotate", 1.0))
        return _from_qt(rotation, translation)
    if kind == "aim":
        source = _eval_expr(expr["input"])
        target = _t(_eval_expr(expr["target"]))
        up = _maya_axis(expr["up_vector"])
        if expr.get("up_space"):
            space = _eval_expr(expr["up_space"])
            up = om.MVector(up) * om.MMatrix(space).homogenize()     # a direction: no translation
        return _aim_matrix(source, _maya_axis(expr["aim_axis"]), target, _maya_axis(expr["up_axis"]), up)
    raise ValueError(kind)


def _pin_matrix(columns, weights, d_weights, halves_world, frame):
    position = om.MVector()
    tangent = om.MVector()
    width = om.MVector()
    for matrix, w, dw, half in zip(columns, weights, d_weights, halves_world):
        center = om.MVector(*_t(matrix))
        position += center * w
        tangent += center * dw
        width += half * w
    tangent = tangent.normal()
    width = (width - tangent * (width * tangent)).normal()
    tangent_axis, width_axis = _maya_axis(frame["tangent_axis"]), _maya_axis(frame["width_axis"])
    local = _frame((0, 0, 0), tangent_axis, width_axis, tangent_axis ^ width_axis)
    world = _frame((0, 0, 0), tangent, width, tangent ^ width)
    result = local.inverse() * world
    for c in range(3):
        result.setElement(3, c, position[c])
    return result


def _ribbon_halves_world(columns, cv_half):
    return [om.MVector(_maya_axis(h)) * om.MMatrix(m).homogenize() for m, h in zip(columns, cv_half)]


def check_j3(setups, manifest):
    """J3: limb records and the IK goal formula (soft IK, stretch, pin, twist) vs Maya."""
    print("\n-- J3 limbs --")
    modules = {m["module_name"]: m for m in manifest["modules"]}
    records = {r["name"]: r for rs in (manifest.get("bone_controllers") or {}).values() for r in rs}
    worst = 0.0
    for setup in (s for s in setups if s["type"] == "ikfk_limb"):
        module = modules[jeffy_reader.module_name(setup)]
        limb = module["params"].get("limb") or {}
        switch = module["params"].get("switch") or {}
        check(switch.get("attribute_info", {}).get("name") == "ikFk", "{}: switch attribute_info".format(module["module_name"]))
        check(switch.get("control", {}).get("parent_space_bone") == module["chain"][-1].split("|")[-1],
              "{}: settings control follows the end joint ({})".format(
                  module["module_name"], switch.get("control", {}).get("parent_space_bone")))
        by_key = {c["key"]: records.get(c["name"]) or {} for c in setup["controls"]}
        check(by_key["fk0"].get("role") == "bone_driver" and (by_key["fk0"].get("parent_space_blend") or {}).get("kind") == "orient",
              "{}: fk0 role/space {}".format(module["module_name"], by_key["fk0"].get("role")))
        check(by_key["ik"].get("role") == "ik_effector" and by_key["pole"].get("role") == "pole_vector",
              "{}: ik/pole roles".format(module["module_name"]))
        check(all("ikFk" not in [a["name"] for a in by_key[k].get("attributes") or []] for k in ("fk0", "ik", "pole")),
              "{}: ikFk proxies dropped".format(module["module_name"]))
        check(limb.get("ik_control") and limb.get("soft") and limb.get("stretch") and limb.get("pin"),
              "{}: limb params {}".format(module["module_name"], limb))

        # The goal formula, in IK mode, against Maya's end joint.
        joints = [j.split("|")[-1] for j in module["chain"]]
        ik, pole = limb["ik_control"], limb["pole_control"]
        settings = switch["control"]["name"]
        previous_mode = cmds.getAttr(settings + ".ikFk")
        cmds.setAttr(settings + ".ikFk", 1.0)
        attrs = limb["attributes"]
        cases = [{}, {"softness": 0.6, "upperLength": 1.3}, {"stretch": 1.0, "lowerLength": 0.7},
                 {"pin": 1.0}, {"stretch": 0.5, "pin": 0.5, "softness": 0.3}, {"twist": 25.0}]
        rest_upper = math.dist(cmds.xform(joints[0], q=True, ws=True, t=True), cmds.xform(joints[1], q=True, ws=True, t=True))
        rest_lower = math.dist(cmds.xform(joints[1], q=True, ws=True, t=True), cmds.xform(joints[2], q=True, ws=True, t=True))
        end_ref = (jeffy_reader._messages(setup["tag"], "ik_end_ref") or [None])[0]
        ik_joints = jeffy_reader._messages(setup["tag"], "ik_joints")
        # pull the IK control so the limb is nearly straight (soft zone active)
        cmds.setAttr(ik + ".translateY", cmds.getAttr(ik + ".translateY") + 90)
        for case in cases:
            touched = {}
            for attr, value in case.items():
                plug = "{}.{}".format(ik, attr)
                touched[plug] = cmds.getAttr(plug)
                cmds.setAttr(plug, value)
            start = om.MVector(*cmds.xform(joints[0], q=True, ws=True, t=True))
            end = om.MVector(*cmds.xform(end_ref, q=True, ws=True, t=True))
            pole_p = om.MVector(*cmds.xform(pole, q=True, ws=True, t=True))
            scale = 1.0
            d = (end - start).length() / scale
            upper = rest_upper * cmds.getAttr(ik + "." + attrs["upper_length"])
            lower = rest_lower * cmds.getAttr(ik + "." + attrs["lower_length"])
            total = upper + lower
            softness = cmds.getAttr(ik + "." + attrs["softness"])
            sd = max(softness * total * 0.25, 1e-4)
            hard = total - sd
            reach = hard + sd * (1 - math.exp(max(-50, min(50, -(d - hard) / sd)))) if d > hard else d
            reach = max(reach, 1e-4)
            stretch, pin = cmds.getAttr(ik + "." + attrs["stretch"]), cmds.getAttr(ik + "." + attrs["pin"])
            factor = 1 + (d / reach - 1) * stretch
            weight = 1 - (1 - stretch) * (1 - pin)
            soft_point = start + (end - start) * (reach * scale / max((end - start).length(), 1e-4))
            goal = soft_point + (end - soft_point) * weight
            finals = [upper * factor, lower * factor]
            finals = [finals[0] + ((start - pole_p).length() - finals[0]) * pin,
                      finals[1] + ((pole_p - end).length() - finals[1]) * pin]
            # The hidden IK chain carries the formula exactly; the bind joints
            # follow it with a constant offset Jeffy baked at build time (0.4
            # cm on the nearly straight arm), which the pose check treats as
            # "explained by the limb being near straight".
            actual_end = om.MVector(*cmds.xform(joints[2], q=True, ws=True, t=True))
            ik_mid = om.MVector(*cmds.xform(ik_joints[1], q=True, ws=True, t=True))
            start = om.MVector(*cmds.xform(ik_joints[0], q=True, ws=True, t=True))
            actual_mid = ik_mid
            err = (actual_end - goal).length()
            err_len = max(abs((actual_mid - start).length() - finals[0]), abs((actual_end - actual_mid).length() - finals[1]))
            if err > 1e-3 or err_len > 1e-3:
                print("   {} {}: goal off {:.4f} cm, lengths off {:.4f} cm (d {:.2f}, reach {:.2f}, L {:.2f})".format(
                    module["module_name"], case, err, err_len, d, reach, total))
            worst = max(worst, err, err_len)
            if "twist" in case:
                # the mid joint turns about the start->end axis by the twist, from the pole side
                axis = (actual_end - start).normal()

                def side(p):
                    v = p - start
                    v -= axis * (v * axis)
                    return v.normal()

                angle = math.degrees(math.atan2((side(pole_p) ^ side(actual_mid)) * axis, side(pole_p) * side(actual_mid)))
                check(abs(angle - 25.0) < 0.05, "{}: twist 25 turned the knee {:.2f} deg (Unreal sign {})".format(
                    module["module_name"], angle, limb["twist_sign_unreal"]))
            for plug, value in touched.items():
                cmds.setAttr(plug, value)
        cmds.setAttr(ik + ".translateY", cmds.getAttr(ik + ".translateY") - 90)
        cmds.setAttr(settings + ".ikFk", previous_mode)
    print("IK goal formula vs Maya (end position, bone lengths): {:.5f} cm".format(worst))
    check(worst < 1e-2, "IK goal formula off by {:.4f} cm".format(worst))


def check_j4(setups, manifest):
    """J4: the ribbon description reproduces the uvPin frames, at rest and posed."""
    print("\n-- J4 ribbons --")
    modules = {m["module_name"]: m for m in manifest["modules"]}
    worst_p = worst_r = 0.0
    poses = [{}, {"L_ArmLeaf1FK_CTL.rotateZ": 35, "L_ArmLeaf2FK_CTL.rotateX": -40, "L_ArmLeaf1Bend_CTL.translateZ": 20,
                  "L_ArmLeaf2Bend_CTL.rotateY": 30, "L_LegLeaf2FK_CTL.rotateZ": 50, "L_LegLeaf1Bend_CTL.rotateX": 25}]
    for setup in (s for s in setups if s["type"] == "ribbon"):
        module = modules[jeffy_reader.module_name(setup)]
        ribbon = module["params"].get("ribbon") or {}
        if not check(ribbon.get("segments"), "{}: no ribbon params ({})".format(
                module["module_name"], module["params"].get("jeffy", {}).get("describe_error"))):
            continue
        check(len(ribbon["segments"]) == 2 and all(len(s["pins"]) == 5 for s in ribbon["segments"]),
              "{}: segments/pins".format(module["module_name"]))
        check(set(ribbon["controls"]) == {"joint1", "bend0", "bend1"}, "{}: controls {}".format(
            module["module_name"], list(ribbon["controls"])))
    rest_joint = {j: _m(j) for j in cmds.ls("*Ribbon??_JNT")}      # the scene is at rest here
    for pose in poses:
        for plug, value in pose.items():
            cmds.setAttr(plug, cmds.getAttr(plug) + value)
        for setup in (s for s in setups if s["type"] == "ribbon"):
            ribbon = modules[jeffy_reader.module_name(setup)]["params"].get("ribbon") or {}
            for entry in ribbon.get("controls", {}).values():
                predicted = _eval_expr(entry["expr"])
                actual = _m(entry["group"])
                worst_p = max(worst_p, (om.MVector(*_t(predicted)) - om.MVector(*_t(actual))).length())
                worst_r = max(worst_r, _rot_err(predicted, actual))
            for segment in ribbon.get("segments", []):
                columns = [_eval_expr(c) for c in segment["columns"]]
                halves = _ribbon_halves_world(columns, segment["cv_half"])
                for pin in segment["pins"]:
                    predicted = _pin_matrix(columns, pin["weights"], pin["d_weights"], halves, ribbon["pin_frame"])
                    # the joint = offset * pin, offset = joint_rest * pin_rest^-1
                    joint_rest = rest_joint.get(pin["joint"])
                    actual_joint = _m(pin["joint"])
                    if joint_rest is None:
                        continue
                    pin_rest = _pose_matrix(pin["pin_rest"])
                    predicted_joint = joint_rest * pin_rest.inverse() * predicted
                    err_p = (om.MVector(*_t(predicted_joint)) - om.MVector(*_t(actual_joint))).length()
                    err_r = _rot_err(predicted_joint, actual_joint)
                    if err_p > 0.01 or err_r > 0.05:
                        print("   {} {}: {:.4f} cm {:.3f} deg".format(ribbon["base_module"], pin["joint"], err_p, err_r))
                    worst_p, worst_r = max(worst_p, err_p), max(worst_r, err_r)
        for plug, value in pose.items():
            cmds.setAttr(plug, cmds.getAttr(plug) - value)
    print("ribbon maths vs Maya (bend groups and 40 joints, rest + posed): {:.5f} cm, {:.4f} deg".format(worst_p, worst_r))
    check(worst_p < 0.01 and worst_r < 0.05, "ribbon maths off ({:.4f} cm, {:.3f} deg)".format(worst_p, worst_r))


def _pose_matrix(pose):
    """A manifest pose (Unreal axes) back to a Maya world matrix."""
    axes = [_maya_axis(a) for a in pose["axes"]]
    x_axis, y_axis, z_axis = axes[0], axes[2], axes[1]
    return _frame(_maya_axis(pose["position"]), x_axis, y_axis, z_axis)


def _bspline_point(knots, degree, t, points):
    weights = jeffy_reader.bspline_basis(knots, degree, t)
    result = om.MVector()
    for w, p in zip(weights, points):
        result += p * w
    return result


def check_j5(setups, manifest):
    """J5: the spline description (columns, chord walk, pin frames) vs Maya's joints."""
    print("\n-- J5 splines --")
    modules = {m["module_name"]: m for m in manifest["modules"]}
    worst_p = worst_r = 0.0
    poses = [{}, {"C_spineIk1_CTL.translateX": 60, "C_spineChestIk_CTL.rotateZ": 30, "C_spineChestIk_CTL.translateY": 40,
                  "C_Spine5IK2_CTL.translateX": 40, "C_Spine5IK3_CTL.rotateY": 45, "C_Spine5IK3_CTL.rotateZ": 20}]
    knots = [0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 4.0, 4.0]
    rest_joint = {}
    for module in modules.values():
        if module["module_type"] == "Jeffy.spline":
            for joint in module["chain"]:
                rest_joint[joint.split("|")[-1]] = _m(joint)                 # the scene is at rest here
    for stretch_value in (1.0, 0.0):
        for pose in poses:
            for plug, value in pose.items():
                cmds.setAttr(plug, cmds.getAttr(plug) + value)
            for setup in (s for s in setups if s["type"] == "spline"):
                module = modules[jeffy_reader.module_name(setup)]
                spline = module["params"].get("jeffy_spline") or {}
                if not check(spline.get("pins"), "{}: no spline params ({})".format(
                        module["module_name"], module["params"].get("jeffy", {}).get("describe_error"))):
                    continue
                if spline.get("stretch"):
                    cmds.setAttr("{}.{}".format(spline["stretch"]["control"], spline["stretch"]["attr"]), stretch_value)
                if module["params"].get("switch"):
                    cmds.setAttr(spline["settings_control"] + ".ikFk", 1.0)
                owners = [_m(n) for n in spline["ik_controls"]]
                centers = [om.MVector(om.MPoint(_maya_axis(c["local"])) * owners[c["owner"]]) for c in spline["columns"]]
                halves = [om.MVector(_maya_axis(h)) * om.MMatrix(owners[c["owner"]]).homogenize()
                          for c, h in zip(spline["columns"], spline["cv_half"])]
                # curve length by sampling
                samples = [_bspline_point(knots, 3, 4.0 * i / 400, centers) for i in range(401)]
                length = sum((b - a).length() for a, b in zip(samples, samples[1:]))
                scale = 1.0
                ratio = length / scale / spline["rest_length"]
                factor = 1 + (ratio - 1) * stretch_value if spline.get("stretch") else ratio
                previous, u = samples[0], 0.0
                for index, pin in enumerate(spline["pins"]):
                    if index:
                        target = spline["bone_lengths"][index - 1] * factor * scale
                        low, high = u, 1.0

                        def chord(v):
                            return (_bspline_point(knots, 3, 4.0 * v, centers) - previous).length()

                        # the FIRST point along the curve at chord distance (a
                        # strong bend can offer a second one further on)
                        steps = 400
                        high = 1.0
                        for k in range(int(u * steps) + 1, steps + 1):
                            if chord(k / steps) >= target:
                                high, low = k / steps, (k - 1) / steps
                                break
                        if chord(high) < target:
                            u = 1.0
                        else:
                            for _ in range(60):
                                mid = (low + high) / 2
                                if chord(mid) < target:
                                    low = mid
                                else:
                                    high = mid
                            u = (low + high) / 2
                    position = _bspline_point(knots, 3, 4.0 * u, centers)
                    d_weights = jeffy_reader.bspline_basis(knots, 3, 4.0 * u, derivative=True)
                    tangent = om.MVector()
                    for w, c in zip(d_weights, centers):
                        tangent += c * w
                    # the strip's width direction at the SAME u: a second curve
                    # through the edge CVs (centre + half) minus the middle one
                    width = om.MVector()
                    for w, h in zip(jeffy_reader.bspline_basis(knots, 3, 4.0 * u), halves):
                        width += h * w
                    tangent = tangent.normal()
                    width = (width - tangent * (width * tangent)).normal()
                    frame = spline["pin_frame"]
                    ta, wa = _maya_axis(frame["tangent_axis"]), _maya_axis(frame["width_axis"])
                    local = _frame((0, 0, 0), ta, wa, ta ^ wa)
                    world = _frame((0, 0, 0), tangent, width, tangent ^ width)
                    predicted_pin = local.inverse() * world
                    for c in range(3):
                        predicted_pin.setElement(3, c, position[c])
                    joint_rest = rest_joint.get(pin["joint"])
                    if joint_rest is None:
                        continue
                    predicted = joint_rest * _pose_matrix(pin["pin_rest"]).inverse() * predicted_pin
                    actual = _m(pin["joint"])
                    err_p = (om.MVector(*_t(predicted)) - om.MVector(*_t(actual))).length()
                    err_r = _rot_err(predicted, actual)
                    if err_p > 0.1 or err_r > 0.5:
                        print("   {} stretch {} {}: {:.3f} cm {:.2f} deg (u {:.4f})".format(
                            module["module_name"], stretch_value, pin["joint"], err_p, err_r, u))
                    worst_p, worst_r = max(worst_p, err_p), max(worst_r, err_r)
                    previous = position
                if spline.get("stretch"):
                    cmds.setAttr("{}.{}".format(spline["stretch"]["control"], spline["stretch"]["attr"]),
                                 spline["stretch"]["value"])
            for plug, value in pose.items():
                cmds.setAttr(plug, cmds.getAttr(plug) - value)
    print("spline maths vs Maya (12 joints, rest + posed, stretch 1 and 0): {:.4f} cm, {:.3f} deg".format(worst_p, worst_r))
    check(worst_p < 0.1 and worst_r < 0.5, "spline maths off ({:.3f} cm, {:.2f} deg)".format(worst_p, worst_r))


def main():
    scene = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="kotsudo_jeffy_")
    cmds.file(scene, open=True, force=True, ignoreVersion=True)

    # -- reader ---------------------------------------------------------------
    setups = jeffy_reader.read_setups()
    summary = jeffy_reader.summary(setups)
    print("setups:", summary)
    check(summary["by_type"] == EXPECTED_TYPES, "setup types {}".format(summary["by_type"]))
    root = erm.root_joint()
    print("root joint:", root)
    check(root and root.split("|")[-1] == "Root", "root joint {}".format(root))
    unknown = [s["tag"] for s in setups if not s["known_version"]]
    check(not unknown, "unknown Jeffy versions: {}".format(unknown))
    no_controls = [s["tag"] for s in setups if s["type"] != "wire_attribute" and not s["controls"]]
    check(not no_controls, "setups without controls: {}".format(no_controls))

    modules = jeffy_reader.modules_config(setups)
    print("modules:", len(modules), collections.Counter(m["module_type"] for m in modules))
    owners = collections.defaultdict(list)
    for module in modules:
        for joint in module["chain"]:
            owners[joint].append(module["module_name"])
    shared = {j.split("|")[-1]: o for j, o in owners.items() if len(o) > 1}
    check(not shared, "joints in two modules: {}".format(list(shared.items())[:5]))
    skinned = set()
    for cluster in cmds.ls(type="skinCluster") or []:
        skinned.update(cmds.ls(cmds.skinCluster(cluster, query=True, influence=True) or [], long=True))
    uncovered = sorted(j for j in skinned if j not in owners)
    print("skinned joints:", len(skinned), "covered by a module:", len(skinned) - len(uncovered))
    # A skinned joint no setup drives is fine when nothing drives it at all:
    # it just follows its parent bone, in Maya and in Unreal alike.
    driven = [j.split("|")[-1] for j in uncovered
              if any(cmds.listConnections("{}.{}".format(j, a), source=True, destination=False)
                     for a in ("translate", "rotate", "scale", "offsetParentMatrix"))]
    print("skinned joints that only follow their parent:", [j.split("|")[-1] for j in uncovered])
    check(not driven, "skinned joints driven by something no setup declares: {}".format(driven))

    # -- tool -------------------------------------------------------------------
    tool_modules = rig_tagger_tool.scene_modules()
    check(len(tool_modules) == len(modules), "tool lists {} modules".format(len(tool_modules)))
    graph = erm.analyze_module_graph(tool_modules)
    statuses = collections.Counter(rig_tagger_tool.validate_module(m, graph)[0] for m in tool_modules)
    print("tool statuses:", dict(statuses))
    supported = sum(1 for m in tool_modules
                    if m["module_type"][len(jeffy_reader.MODULE_PREFIX):] in jeffy_reader.UNREAL_SUPPORTED_TYPES)
    check(statuses.get("green", 0) == supported and statuses.get("orange", 0) == len(tool_modules) - supported,
          "tool statuses {} ({} supported)".format(dict(statuses), supported))
    described = [m for m in tool_modules if m["module_type"] in ("Jeffy.ikfk_limb", "Jeffy.ribbon", "Jeffy.spline")]
    missing = [m["module_name"] for m in described
               if not any(k in m["params"] for k in ("limb", "ribbon", "jeffy_spline"))]
    check(not missing, "setups without an Unreal description: {} ({})".format(
        missing, [m["params"].get("jeffy", {}).get("describe_error") for m in described
                  if m["module_name"] in missing]))

    # -- full export ---------------------------------------------------------------
    cmds.loadPlugin("fbxmaya", quiet=True)
    fbx = erm.export(out_dir, "v010", "murakami", tool_modules)
    manifest = json.loads(cmds.getAttr(erm.manifest_plug()))
    jeffy = manifest.get("jeffy") or {}
    records = [c for s in jeffy.get("setups") or [] for c in s.get("controls") or []]
    with_shape = sum(1 for c in records if c.get("shape_id"))
    print("manifest: {} modules, jeffy setups {}, control records {} ({} with a shape), notes {}".format(
        len(manifest["modules"]), len(jeffy.get("setups") or []), len(records), with_shape, jeffy.get("notes")))
    check(len(jeffy.get("setups") or []) == summary["setups"], "jeffy setups in manifest")
    check(manifest.get("source", {}).get("rig_tool") == "jeffy", "source.rig_tool")
    found = etp.manifest_controls(manifest)
    print("animator controls the pose check finds:", len(found))
    check(len(found) >= summary["controls"] - 4, "pose check finds {} of {} controls".format(
        len(found), summary["controls"]))
    print("export summary:", erm.LAST_EXPORT_SUMMARY)
    poses = (erm.LAST_EXPORT_SUMMARY.get("poses") or {})
    check(poses.get("count", 0) > 100, "test poses: {}".format(poses))
    check(os.path.isfile(fbx), "FBX written")

    check_j2(setups, manifest)
    check_j3(setups, manifest)
    check_j4(setups, manifest)
    check_j5(setups, manifest)

    # -- an animation clip on Jeffy controls -----------------------------------------------
    rng = random.Random(5)
    keyed = 0
    for name in sorted(found)[:12]:
        node = found[name]
        for attr in [a for a in ("rotateX", "rotateY", "rotateZ")
                     if a in (cmds.listAttr(node, keyable=True, unlocked=True, scalar=True) or [])][:2]:
            for frame in (0, 12, 24):
                cmds.setKeyframe(node, attribute=attr, time=frame, value=rng.uniform(-20, 20))
            keyed += 1
    clip = ea.export_clip(manifest, out_dir, "j0", 0, 24, bake_fbx=True)
    doc = json.load(open(clip["json"]))
    print("clip:", {k: clip[k] for k in ("frames", "keyed_channels", "keyed_controls", "controls")})
    check(clip["keyed_channels"] == keyed, "clip keyed channels {} (keyed {})".format(clip["keyed_channels"], keyed))
    worst_p = worst_r = 0.0
    for index in (5, 17):
        cmds.currentTime(doc["time"]["frames"][index], update=True)
        for name in list(found)[:20]:
            t, q = etp.world_state(found[name])
            st, sq = doc["controls"][name]["samples"][index]
            worst_p, worst_r = max(worst_p, math.dist(t, st)), max(worst_r, q_angle(q, sq))
        for joint in erm._exported_joints()[:40]:
            t, q = etp.world_state(joint)
            st, sq = doc["joints"][erm._short_node_name(joint)][index]
            worst_p, worst_r = max(worst_p, math.dist(t, st)), max(worst_r, q_angle(q, sq))
    print("clip samples vs fresh capture: {:.6f} cm, {:.5f} deg".format(worst_p, worst_r))
    check(worst_p < 1e-3 and worst_r < 1e-2, "clip samples differ")

    print("\n{} failure(s)".format(len(FAILURES)))
    for failure in FAILURES:
        print("  - " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    os._exit(code)
