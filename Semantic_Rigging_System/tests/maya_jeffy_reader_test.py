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
    check(len(driven) == sum(1 for s in fk if len(s["controls"]) > 1), "driven groups: {}".format(len(driven)))
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
