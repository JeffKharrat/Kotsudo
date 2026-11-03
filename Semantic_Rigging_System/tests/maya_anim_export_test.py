"""Animation clip export, checked against Maya itself (run with mayapy on a scene copy).

    "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya_anim_export_test.py <murakami_copy.ma> [out_dir]

Keys each script-made clip (tools/maya/test_animations.py) on Murakami,
exports it, and checks the sidecar against what Maya computes:

  * frames, rate and range;
  * the rest pose equals the un-animated rest (the pose check's), even when
    world_ctrl or keyed controls are away from it;
  * at random frames, control and joint world states equal a fresh capture;
  * keyed channels: every key Maya has, read only from time-based curves;
    rebuilt as a cubic Hermite curve from the exported slopes, they match
    Maya's curve between keys (unweighted curves), and stepped keys hold;
  * channel sources: an anim-layer channel is "layered", driven keys are
    never "keys"; an animated world_ctrl is reported on its controls;
  * the baked FBX holds the skeleton and animation curves.

The scene is never saved.
"""
import json
import math
import os
import random
import sys
import tempfile

import maya.standalone

maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "maya"))
import export_animation as ea  # noqa: E402
import export_rig_manifest as erm  # noqa: E402
import export_test_poses as etp  # noqa: E402
import test_animations as ta  # noqa: E402

FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)
        print("  FAIL " + message)
    return condition


def q_angle(a, b):
    dot = min(1.0, abs(sum(x * y for x, y in zip(a, b))) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))))
    return math.degrees(2.0 * math.acos(dot))


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def hermite(keys, frame):
    """Value of the exported curve at ``frame`` (cubic Hermite per segment,
    slopes per frame; stepped keys hold), constant outside the keys."""
    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    for k0, k1 in zip(keys, keys[1:]):
        if k0[0] <= frame <= k1[0]:
            if k0[3] in ("step",):
                return k0[1] if frame < k1[0] else k1[1]
            dt = k1[0] - k0[0]
            t = (frame - k0[0]) / dt
            t2, t3 = t * t, t * t * t
            return ((2 * t3 - 3 * t2 + 1) * k0[1] + (t3 - 2 * t2 + t) * k0[5] * dt
                    + (-2 * t3 + 3 * t2) * k1[1] + (t3 - t2) * k1[4] * dt)
    return keys[-1][1]


def main():
    scene = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="kotsudo_anim_")
    cmds.file(scene, open=True, force=True, ignoreVersion=True)
    manifest = ea.manifest_from_scene()
    controls = etp.manifest_controls(manifest)
    print("Scene: {}  controls: {}  out: {}".format(scene, len(controls), out_dir))

    ta.clear_animation()
    reference_rest = ea.control_rest(controls, manifest)
    rng = random.Random(3)

    for clip in ta.CLIPS:
        print("\n== clip {} ==".format(clip))
        start, end = ta.make_clip(clip)
        bake = clip in ("fk_body", "world")
        summary = ea.export_clip(manifest, out_dir, clip, start, end, bake_fbx=bake)
        with open(summary["json"]) as handle:
            doc = json.load(handle)

        # -- time ------------------------------------------------------------
        check(doc["time"]["fps"] == 24.0 and doc["time"]["maya_unit"] == "film",
              "{}: rate {} {}".format(clip, doc["time"]["fps"], doc["time"]["maya_unit"]))
        check(doc["time"]["frames"][0] == start and doc["time"]["frames"][-1] == end
              and len(doc["time"]["frames"]) == end - start + 1, "{}: frame range".format(clip))

        # -- rest --------------------------------------------------------------
        worst_p = max(distance(doc["control_rest"][n]["t"], reference_rest[n]["t"]) for n in reference_rest)
        worst_r = max(q_angle(doc["control_rest"][n]["q"], reference_rest[n]["q"]) for n in reference_rest)
        print("  rest vs un-animated rest: {:.5f} cm, {:.5f} deg".format(worst_p, worst_r))
        check(worst_p < 1e-3 and worst_r < 1e-2, "{}: rest differs ({:.4f} cm, {:.4f} deg)".format(
            clip, worst_p, worst_r))

        # -- per-frame samples against a fresh capture ---------------------------
        frames = doc["time"]["frames"]
        joints = erm._exported_joints()
        sample_p = sample_r = 0.0
        for index in sorted(rng.sample(range(len(frames)), 3)):
            cmds.currentTime(frames[index], update=True)
            for name in rng.sample(sorted(controls), 8):
                t, q = etp.world_state(controls[name])
                st, sq = doc["controls"][name]["samples"][index]
                sample_p, sample_r = max(sample_p, distance(t, st)), max(sample_r, q_angle(q, sq))
            for joint in rng.sample(joints, 15):
                t, q = etp.world_state(joint)
                st, sq = doc["joints"][erm._short_node_name(joint)][index]
                sample_p, sample_r = max(sample_p, distance(t, st)), max(sample_r, q_angle(q, sq))
        print("  samples vs fresh capture: {:.6f} cm, {:.5f} deg".format(sample_p, sample_r))
        check(sample_p < 1e-3 and sample_r < 1e-2, "{}: samples differ".format(clip))

        # -- keys ------------------------------------------------------------------
        curve_error, compared, weighted = 0.0, 0, 0
        for name, control in doc["controls"].items():
            for attribute, record in control["channels"].items():
                plug = "{}.{}".format(controls[name], attribute)
                if record["source"] == "keys":
                    check(record["type"].startswith("animCurveT"), "{}: {} keys from {}".format(
                        clip, plug, record["type"]))
                    maya_times = cmds.keyframe(record["curve"], query=True, timeChange=True) or []
                    check([k[0] for k in record["keys"]] == [round(t, 6) for t in maya_times],
                          "{}: {} key times".format(clip, plug))
                    if record["weighted"]:
                        weighted += 1
                        continue
                    scale = ea.channel_value_scale(controls[name], attribute)
                    keys = record["keys"]
                    for k0, k1 in zip(keys, keys[1:]):
                        for frac in (0.25, 0.5, 0.75):
                            f = k0[0] + frac * (k1[0] - k0[0])
                            maya = cmds.keyframe(record["curve"], query=True, eval=True, time=(f, f))[0] * scale
                            curve_error = max(curve_error, abs(hermite(keys, f) - maya))
                            compared += 1
                    for index, frame in enumerate(frames):
                        maya = cmds.keyframe(record["curve"], query=True, eval=True,
                                             time=(frame, frame))[0] * scale
                        if not check(abs(record["values"][index] - maya) < 1e-3,
                                     "{}: {} value at {}".format(clip, plug, frame)):
                            break
        print("  keyed channels: {}  keys: {}  curve rebuilt from slopes: max error {:.6f} "
              "over {} points ({} weighted curve(s) skipped)".format(
                  summary["keyed_channels"], summary["keys"], curve_error, compared, weighted))
        check(summary["keyed_channels"] > 0, "{}: nothing keyed".format(clip))
        check(curve_error < 1e-3, "{}: Hermite rebuild off by {:.5f}".format(clip, curve_error))

        # -- clip-specific -----------------------------------------------------------
        if clip == "stress":
            neck = doc["controls"]["neck_ctrl_01"]["channels"]["rotateZ"]
            check(neck["source"] == "layered", "stress: neck layer seen as {}".format(neck["source"]))
            petal = doc["controls"]["Head_Settings_Ctrl"]["channels"].get("Petal_Rotation")
            if petal:
                check(petal["source"] == "keys" and petal["keys"][0][3] == "step",
                      "stress: stepped slider keys")
            head = doc["controls"]["head_ctrl"]["channels"]["rotateY"]["values"]
            check(abs(head[-1] - 360.0) < 1e-3, "stress: head spin ends at {}".format(head[-1]))
            chest = doc["controls"]["chest_ctrl"]
            check(chest["rotate_order"] == 2, "stress: chest rotate order {}".format(chest["rotate_order"]))
            check(18.5 in [k[0] for k in chest["channels"]["rotateZ"]["keys"]], "stress: sub-frame key")
            check(doc["controls"]["L_ArmFKctrl_01"]["channels"]["rotateZ"]["weighted"],
                  "stress: weighted curve")
        if clip == "world":
            moved = [n for n, c in doc["controls"].items() if "world_ctrl" in (c.get("parents_animated") or [])]
            print("  controls under the animated world_ctrl: {}".format(len(moved)))
            check(moved, "world: world_ctrl motion not reported")
            check(any("world_ctrl" in note for note in doc["notes"]), "world: no note")
        if clip == "ikfk_switch":
            values = doc["switch_values"]["L_Arm_IKFK_Switch.IK_FK"]
            check(values[0] == 0.0 and values[-1] == 1.0, "ikfk: switch values {}..{}".format(values[0], values[-1]))
        if bake:
            fbx = summary["fbx"]
            with open(fbx, "rb") as handle:
                data = handle.read()
            check(b"AnimationCurve" in data, "{}: no animation curves in the FBX".format(clip))
            print("  baked FBX: {:.1f} MB".format(len(data) / 1e6))
        for note in doc["notes"]:
            print("  note: " + note)

    ta.clear_animation()
    print("\n{} failure(s)".format(len(FAILURES)))
    for failure in FAILURES:
        print("  - " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    maya.standalone.uninitialize()
    os._exit(code)
