"""Read a rig built with the Jeffy toolkit (Jeff Inventory) into Kotsudo terms.

Jeffy (https://github.com/JeffKharrat/Jeffy-Toolkit) describes every setup it
builds on a ``network`` node (a *tag*, ``<side>_<name>_SETUP``):

* ``jeffySetupType`` / ``jeffySetupName`` / ``jeffySetupSide`` /
  ``jeffySetupVersion``;
* JSON strings ``jeffy_options``, ``jeffy_slots`` (the joints it was built
  on), ``jeffy_anchors`` (spaces: kind, roles, enum attribute, rest matrix),
  ``jeffy_rest`` (rest channels of the driven joints);
* message lists ``jeffyMsg_<key>``: ``controls``, ``outputs``, ``driven``...

Every bind joint copies the world matrix of one **output** transform
(``joint.world = output.world``; output -> joint link: ``output.jeffyJoint``).
Controls carry ``jeffyKey`` (``fk0``, ``ik``, ``pole``, ``settings``...) and
optionally ``jeffySpaceRole`` (``cog``, ``chest``...).

This module only reads (it never imports Jeffy, which needs not be
installed). It gives the exporter:

* :func:`root_joint` -- the skeleton root the ``rig_base`` setup declares;
* :func:`read_setups` -- every setup with its controls and outputs;
* :func:`modules_config` -- one Kotsudo module per setup that drives joints
  (``module_type`` = ``Jeffy.<setup type>``, chain = driven joints in output
  order), for the exporter, the tool and the pose / animation checks;
* :func:`manifest_section` -- the ``jeffy`` block of the manifest (setups,
  options, spaces, control records with Kotsudo controller snapshots).

See docs/jeffy-v010-support.md.
"""

import json

import maya.cmds as cmds

TYPE_ATTR = "jeffySetupType"
KEY_ATTR = "jeffyKey"
ROLE_ATTR = "jeffySpaceRole"
REST_ATTR = "jeffyRest"
OUTPUT_JOINT_ATTR = "jeffyJoint"
MODULE_PREFIX = "Jeffy."
#: setup versions this reader was written against (others are read, and reported)
KNOWN_VERSIONS = {1}
#: Jeffy setup types the Unreal builder rebuilds (filled phase by phase, J2-J5).
#: Their controls are described as standard Kotsudo controller records
#: (``bone_controllers``: parents, spaces, driven groups); ``wire_attribute``
#: goes through the blendshape export.
UNREAL_SUPPORTED_TYPES = {"fk_chain", "rig_base", "wire_attribute"}
DESCRIBED_TYPES = ("fk_chain", "rig_base")
#: IK/FK switch: the ``ikFk`` attribute of a setup's ``settings`` control
#: (1 = IK, 0 = FK; measured on V010: at 0 only the FK controls move the joints)
SWITCH_ATTR = "ikFk"
SWITCH_IK_VALUE, SWITCH_FK_VALUE = 1.0, 0.0


def _short(node):
    return node.split("|")[-1] if node else node


def _long(node):
    found = cmds.ls(node, long=True) if node else []
    return found[0] if found else node


def _string(node, attr, default=None):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return default
    try:
        value = cmds.getAttr("{}.{}".format(node, attr))
    except Exception:
        return default
    return value if value is not None else default


def _json(node, attr, default=None):
    text = _string(node, "jeffy_" + attr)
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _messages(node, key, keep_empty=False):
    """Long names of a ``jeffyMsg_<key>`` message list, in index order."""
    attr = "jeffyMsg_" + key
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return []
    plug = "{}.{}".format(node, attr)
    indices = cmds.getAttr(plug, multiIndices=True) or []
    if not indices:
        return []
    result = [None] * (max(indices) + 1) if keep_empty else []
    for index in indices:
        sources = cmds.listConnections("{}[{}]".format(plug, index), source=True, destination=False,
                                       fullNodeName=True) or []
        source = _long(sources[0]) if sources else None
        if keep_empty:
            result[index] = source
        elif source:
            result.append(source)
    return result


# ---------------------------------------------------------------------------
# Setups
# ---------------------------------------------------------------------------

def setup_nodes():
    """Every Jeffy setup tag in the scene, sorted by name."""
    return sorted(n for n in cmds.ls(type="network") or []
                  if cmds.attributeQuery(TYPE_ATTR, node=n, exists=True))


def is_jeffy_rig():
    return bool(setup_nodes())


def _control_record(node):
    return {
        "node": node,
        "name": _short(node),
        "key": _string(node, KEY_ATTR),
        "space_role": _string(node, ROLE_ATTR),
    }


def _output_record(node):
    joints = []
    if cmds.attributeQuery(OUTPUT_JOINT_ATTR, node=node, exists=True):
        joints = cmds.listConnections("{}.{}".format(node, OUTPUT_JOINT_ATTR), source=True, destination=False,
                                      fullNodeName=True) or []
    joint = _long(joints[0]) if joints else None
    return {"node": node, "name": _short(node), "key": _string(node, KEY_ATTR), "joint": joint}


def read_setup(tag):
    """One setup as a plain dict (long node names)."""
    anchors = []
    zeros = _messages(tag, "anchor_zero", keep_empty=True)
    anchor_controls = _messages(tag, "anchor_control", keep_empty=True)
    for index, definition in enumerate(_json(tag, "anchors", []) or []):
        entry = dict(definition)
        entry["zero"] = zeros[index] if index < len(zeros) else None
        entry["control"] = anchor_controls[index] if (definition.get("attr") and index < len(anchor_controls)) \
            else None
        anchors.append(entry)
    version = _string(tag, "jeffySetupVersion")
    return {
        "tag": tag,
        "type": _string(tag, TYPE_ATTR),
        "name": _string(tag, "jeffySetupName"),
        "side": _string(tag, "jeffySetupSide"),
        "version": version,
        "known_version": version in KNOWN_VERSIONS,
        "options": _json(tag, "options", {}) or {},
        "slots": _json(tag, "slots", {}) or {},
        "guides": _json(tag, "guides", {}) or {},
        "anchors": anchors,
        "rest": _json(tag, "rest", []) or [],
        "depends": _json(tag, "depends_keys", []) or [],
        "controls": [_control_record(n) for n in _messages(tag, "controls")],
        "outputs": [_output_record(n) for n in _messages(tag, "outputs")],
        "driven": _messages(tag, "driven"),
        "driven_plugs": _json(tag, "driven_plugs", []) or [],
    }


def read_setups():
    return [read_setup(tag) for tag in setup_nodes()]


def module_name(setup):
    """Kotsudo module name: ``<side>_<name>`` (``C_Blue1``, ``L_ArmLeaf1``)."""
    side = setup.get("side") or "C"
    return "{}_{}".format(side, setup.get("name") or setup["tag"])


def root_joint():
    """Long name of the skeleton root the ``rig_base`` setup declares, or None."""
    for tag in setup_nodes():
        if _string(tag, TYPE_ATTR) != "rig_base":
            continue
        name = (_json(tag, "slots", {}) or {}).get("root_joint")
        found = [n for n in (cmds.ls(name, long=True) or []) if cmds.nodeType(n) == "joint"] if name else []
        if len(found) == 1:
            return found[0]
    return None


# ---------------------------------------------------------------------------
# Kotsudo views
# ---------------------------------------------------------------------------

def driven_chain(setup):
    """[(joint, output record)] of a setup, in output order (joints once)."""
    chain, seen = [], set()
    for output in setup["outputs"]:
        joint = output.get("joint")
        if joint and joint not in seen:
            seen.add(joint)
            chain.append((joint, output))
    return chain


def modules_config(setups=None):
    """Kotsudo modules: one per Jeffy setup that drives bind joints.

    Setups that drive no joint (``wire_attribute``: attribute -> attribute)
    stay in the manifest's ``jeffy`` section only.
    """
    setups = read_setups() if setups is None else setups
    modules = []
    for setup in setups:
        chain = driven_chain(setup)
        if not chain:
            continue
        joints = [joint for joint, _ in chain]
        modules.append({
            "module_type": MODULE_PREFIX + str(setup["type"]),
            "module_name": module_name(setup),
            "chain": joints,
            "chain_items": [{"bone_name": joint, "role": output.get("key") or ""} for joint, output in chain],
            "start_bone": joints[0],
            "end_bone": joints[-1],
            "source": "jeffy",
            "jeffy_tag": setup["tag"],
            "params": dict({"jeffy": {"type": setup["type"], "tag": setup["tag"], "version": setup["version"],
                                      "options": setup["options"]}}, **_switch_params(setup, joints)),
        })
    return modules


def _switch_params(setup, joints):
    """``{"switch": ...}`` (as on tagged rigs) when the setup has an IK/FK
    switch, else ``{}``: the clip export and the pose check use it."""
    for control in setup["controls"]:
        node = control["node"]
        if control.get("key") != "settings" or not cmds.attributeQuery(SWITCH_ATTR, node=node, exists=True):
            continue
        value = float(cmds.getAttr("{}.{}".format(node, SWITCH_ATTR)))
        span = SWITCH_IK_VALUE - SWITCH_FK_VALUE
        return {"switch": {
            "control": {"name": control["name"], "role": "settings", "anchor_bone": _short(joints[0])},
            "attribute": SWITCH_ATTR,
            "ik_value": SWITCH_IK_VALUE,
            "fk_value": SWITCH_FK_VALUE,
            "default_ik_weight": round(max(0.0, min(1.0, (value - SWITCH_FK_VALUE) / span)), 4),
        }}
    return {}


def wired_blendshapes():
    """blendShape nodes a ``wire_attribute`` setup drives."""
    nodes = set()
    for tag in setup_nodes():
        if _string(tag, TYPE_ATTR) != "wire_attribute":
            continue
        for target in (_json(tag, "slots", {}) or {}).get("targets") or []:
            nodes.add(str(target).split(".")[0])
    return nodes


# ---------------------------------------------------------------------------
# Controller records (Kotsudo terms) for the Unreal builder
# ---------------------------------------------------------------------------

class _Index:
    """Lookups over every setup: controls, space groups, outputs."""

    def __init__(self, setups):
        self.setups = setups
        self.controls = {}       # long name -> (setup, control)
        self.space_groups = {}   # long name of an anchor's zero group -> (setup, anchor)
        for setup in setups:
            for control in setup["controls"]:
                self.controls[_long(control["node"])] = (setup, control)
            for anchor in setup["anchors"]:
                if anchor.get("zero") and anchor.get("attr"):
                    self.space_groups[_long(anchor["zero"])] = (setup, anchor)


def _parent(node):
    return (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]


def _matrix_sources(plug):
    return cmds.listConnections(plug, source=True, destination=False, plugs=True) or []


def _source_control(node, index, depth=0):
    """The Jeffy control whose world matrix places ``node`` (an output),
    through multMatrix nodes and offsetParentMatrix inputs, or None."""
    if depth > 12:
        return None
    plugs = ["{}.offsetParentMatrix".format(node)] if cmds.attributeQuery(
        "offsetParentMatrix", node=node, exists=True) else []
    if cmds.nodeType(node) == "multMatrix":
        plugs = ["{}.matrixIn[{}]".format(node, i) for i in cmds.getAttr(node + ".matrixIn", multiIndices=True) or []]
    for plug in plugs:
        for source in _matrix_sources(plug):
            source_node, _, attribute = source.partition(".")
            long_name = _long(source_node)
            if long_name in index.controls and attribute.startswith("worldMatrix"):
                return long_name
            if cmds.nodeType(source_node) == "multMatrix" or attribute.startswith("worldMatrix"):
                found = _source_control(source_node, index, depth + 1)
                if found:
                    return found
    return None


def _record_role(setup, control, index):
    """(Kotsudo role, driven bone): a control that places an output drives
    that output's joint ('bone_driver'); the others keep their Jeffy key."""
    node = _long(control["node"])
    for output in setup["outputs"]:
        if output.get("joint") and _source_control(output["node"], index) == node:
            return "bone_driver", output["joint"]
    return control.get("key") or "control", None


def _space_target(node, index):
    """What a space target follows in the rebuilt rig."""
    long_name = _long(node)
    if long_name in index.controls:
        return {"controller": _short(long_name)}
    if cmds.attributeQuery(OUTPUT_JOINT_ATTR, node=node, exists=True):
        joints = cmds.listConnections("{}.{}".format(node, OUTPUT_JOINT_ATTR), source=True,
                                      destination=False) or []
        if joints:
            return {"bone": _short(joints[0])}
    return {"world": True}


def _space_blend(control_node, anchor, index):
    """The anchor's space switch in Kotsudo's blended-space form: one target
    per space (``offset x target.world``, picked by the enum through a choice
    node), weight = (enum == space), and per space the pose Maya gives the
    control with that space alone ('solo'). Returns (blend, reason)."""
    import export_rig_manifest as erm

    attribute = anchor["attr"]
    plug = "{}.{}".format(control_node, attribute)
    choices = [c for c in (cmds.listConnections(plug, source=False, destination=True, type="choice") or [])]
    if len(set(choices)) != 1:
        return None, "{} drives {} choice node(s)".format(plug, len(set(choices)))
    choice = choices[0]
    targets = []
    for i in cmds.getAttr(choice + ".input", multiIndices=True) or []:
        target_node = None
        for source in _matrix_sources("{}.input[{}]".format(choice, i)):
            mult = source.partition(".")[0]
            if cmds.nodeType(mult) != "multMatrix":
                continue
            for j in cmds.getAttr(mult + ".matrixIn", multiIndices=True) or []:
                for matrix in _matrix_sources("{}.matrixIn[{}]".format(mult, j)):
                    if matrix.partition(".")[2].startswith("worldMatrix"):
                        target_node = matrix.partition(".")[0]
        if target_node is None:
            return None, "space {} of {} has no target".format(i, plug)
        target = _space_target(target_node, index)
        target.update({"node": _short(target_node), "index": i,
                       "weight": {"control": _short(control_node), "attr": attribute,
                                  "ops": [{"op": "compare", "test": "eq", "value": float(i),
                                           "true": 1.0, "false": 0.0}]}})
        targets.append(target)
    blend = {"kind": anchor.get("kind") or "parent", "group": _short(anchor.get("zero")),
             "constraint": _short(choice), "targets": targets, "source": "jeffy",
             # A target whose setup is not rebuilt yet holds its space still.
             "missing_targets": "static",
             "rest": erm._world_pose_unreal(control_node)}
    previous = cmds.getAttr(plug)
    try:
        for target in targets:
            cmds.setAttr(plug, target["index"])
            target["solo"] = erm._world_pose_unreal(control_node)
    finally:
        cmds.setAttr(plug, previous)
    labels = {value: label for label, value in erm.enum_entries(_short(control_node), attribute)}
    blend["switch"] = {"control": _short(control_node), "attr": attribute,
                       "values": [float(t["index"]) for t in targets],
                       "labels": [labels.get(float(t["index"]), "?") for t in targets]}
    return blend, None


_COMPOSE_INPUTS = {"translate" + a: "inputTranslate" + a for a in "XYZ"}
_COMPOSE_INPUTS.update({"rotate" + a: "inputRotate" + a for a in "XYZ"})


def _compose_group_entry(group, compose):
    """An offset group placed by a composeMatrix (offsetParentMatrix): a
    driven group when attributes feed the compose inputs (``length``
    scaling the next control's offset), else static."""
    import export_rig_manifest as erm

    entry = {"name": _short(group), "node_type": cmds.nodeType(group), "kind": "static"}
    driven, problems = {}, []
    for channel, compose_input in _COMPOSE_INPUTS.items():
        plug = "{}.{}".format(compose, compose_input)
        if erm._source_plug(plug) is None:
            continue
        control, attribute, ops = erm._attribute_driver(plug)
        if control is None:
            problems.append("{}: {}".format(channel, ops))
        else:
            driven[channel] = {"control": control, "attr": attribute, "ops": ops}
    for other in ("inputScale", "inputQuat", "inputShear", "inputRotateOrder"):
        if _matrix_sources("{}.{}".format(compose, other)):
            problems.append("{}.{} is driven (not transferred)".format(_short(compose), other))
    if any(abs(c) > 1e-9 for c in cmds.getAttr(group + ".translate")[0] + cmds.getAttr(group + ".rotate")[0]):
        problems.append("its own translate/rotate are not zero (not transferred)")
    if not driven and not problems:
        return entry
    entry.update({"kind": "driven", "driven": driven, "problems": problems,
                  "rest_world": erm._world_pose_unreal(group),
                  "rest": {c: float(cmds.getAttr("{}.{}".format(compose, i))) for c, i in _COMPOSE_INPUTS.items()},
                  "rotate_order": erm._ROTATE_ORDERS[cmds.getAttr(compose + ".inputRotateOrder")],
                  "rotate_axis": [0.0, 0.0, 0.0], "joint_orient": [0.0, 0.0, 0.0],
                  "axes_unreal": {a: erm._maya_vector_to_unreal(v, apply_unit_scale=False)
                                  for a, v in (("x", [1, 0, 0]), ("y", [0, 1, 0]), ("z", [0, 0, 1]))},
                  "unit_scale": erm._maya_linear_to_centimeters_scale()})
    if not driven:
        entry["kind"] = "static"
    return entry


def _describe_hierarchy(record, node, index):
    """Fill what the generic snapshot cannot see through Jeffy's matrix
    groups: the parent controllers, the space switch and the groups."""
    ancestors, current = [], _parent(node)
    while current:
        ancestors.append(current)
        current = _parent(current)
    # Parent controllers: the Jeffy controls above, up to a space group (a
    # space replaces everything above it).
    parents = []
    space_group = None
    for ancestor in ancestors:
        if ancestor in index.space_groups:
            space_group = ancestor
            break
        if ancestor in index.controls:
            parents.append(_short(ancestor))
    record["parent_controllers"] = parents
    record["parent_controller"] = parents[0] if parents else None

    by_short = {_short(a): a for a in ancestors}
    stack = []
    for entry in record.get("offset_stack") or []:
        group = by_short.get(entry.get("name"))
        if entry.get("kind") == "matrix" and group:
            if group in index.space_groups:
                entry = {"name": entry["name"], "node_type": entry.get("node_type"), "kind": "space"}
            else:
                sources = _matrix_sources(group + ".offsetParentMatrix")
                compose = sources[0].partition(".")[0] if sources else None
                if compose and cmds.nodeType(compose) == "composeMatrix":
                    entry = _compose_group_entry(group, compose)
        stack.append(entry)
    record["offset_stack"] = stack

    record.pop("parent_space_note", None)
    record["parent_space_blend"] = None
    if space_group and not parents:
        setup, anchor = index.space_groups[space_group]
        if anchor.get("control") and _long(anchor["control"]) == node:
            blend, reason = _space_blend(node, anchor, index)
            record["parent_space_blend"] = blend
            if reason:
                record["parent_space_note"] = reason
            bones = [t["bone"] for t in (blend or {}).get("targets") or [] if t.get("bone")]
            if bones:
                record["parent_space_bone"] = bones[0]


def control_record(setup, control, index):
    """A Kotsudo controller record for one Jeffy control (the snapshot the
    exporter makes for any controller, plus Jeffy's key, space role and
    setup; for the described types, also parents, spaces and groups)."""
    import export_rig_manifest as erm

    name = module_name(setup)
    node = _long(control["node"])
    anchor = _anchor_bone(setup, control)
    role, driven = (_record_role(setup, control, index) if setup["type"] in DESCRIBED_TYPES
                    else (control.get("key") or "control", None))
    snapshot = erm._query_transform_snapshot(node, role, name, driven or anchor, driven or anchor)
    record = dict(snapshot or {"name": control["name"], "dag_path": node})
    record.update({"jeffy_key": control.get("key"), "jeffy_space_role": control.get("space_role"),
                   "jeffy_setup": name, "anchor_bone": _short(driven or anchor) if (driven or anchor) else None})
    if setup["type"] in DESCRIBED_TYPES:
        _describe_hierarchy(record, node, index)
    return record


def bone_controller_entries(module, setups=None):
    """[(bone, controller record)] of a Jeffy module of a described type,
    for the manifest's ``bone_controllers`` (keyed by the bone each control
    drives, else the module's first bone)."""
    setups = read_setups() if setups is None else setups
    setup = next((s for s in setups if s["tag"] == module.get("jeffy_tag")), None)
    if setup is None or setup["type"] not in DESCRIBED_TYPES:
        return []
    index = _Index(setups)
    entries = []
    for control in setup["controls"]:
        record = control_record(setup, control, index)
        entries.append((record.get("driven_bone") or module["chain"][0], record))
    return entries


def _anchor_bone(setup, control):
    """The bind joint a control is measured from: the joint of the output with
    the same index for FK controls (``fk2`` -> ``j2``), else the first one."""
    chain = driven_chain(setup)
    if not chain:
        return None
    key = control.get("key") or ""
    if key.startswith("fk") and key[2:].isdigit():
        index = int(key[2:])
        if index < len(chain):
            return chain[index][0]
    return chain[0][0]


def _json_safe(value):
    """Matrices and such as plain lists (Maya returns tuples)."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def manifest_section(setups=None):
    """The manifest's ``jeffy`` block: every setup, with control records.

    Each control record is a Kotsudo controller snapshot (origin, offset from
    its anchor bone in Unreal axes, shape, attributes, locks...) plus Jeffy's
    key, space role and the setup it belongs to, so the pose and animation
    checks find the animator controls as they do on tagged rigs.
    """
    import export_rig_manifest as erm

    setups = read_setups() if setups is None else setups
    index = _Index(setups)
    section = {"setups": [], "notes": []}
    for setup in setups:
        name = module_name(setup)
        controls = []
        for control in setup["controls"]:
            try:
                record = control_record(setup, control, index)
            except Exception as exc:
                section["notes"].append("{}: no controller snapshot ({})".format(control["name"], exc))
                record = {"name": control["name"], "dag_path": control["node"], "jeffy_key": control.get("key"),
                          "jeffy_setup": name}
            controls.append(record)
        section["setups"].append(_json_safe({
            "module_name": name,
            "type": setup["type"],
            "tag": setup["tag"],
            "version": setup["version"],
            "options": setup["options"],
            "slots": setup["slots"],
            "anchors": [dict(a, zero=_short(a.get("zero")), control=_short(a.get("control")))
                        for a in setup["anchors"]],
            "outputs": [{"key": o["key"], "output": o["name"], "joint": _short(o.get("joint"))}
                        for o in setup["outputs"]],
            "driven_plugs": setup["driven_plugs"],
            "controls": controls,
        }))
        if not setup["known_version"]:
            section["notes"].append("{}: Jeffy setup version {} is newer than this reader knows (1); "
                                    "read as version 1".format(name, setup["version"]))
    return section


def summary(setups=None):
    """Counts for logs and the tool: setups by type, controls, driven joints."""
    setups = read_setups() if setups is None else setups
    by_type = {}
    for setup in setups:
        by_type[setup["type"]] = by_type.get(setup["type"], 0) + 1
    return {
        "setups": len(setups),
        "by_type": dict(sorted(by_type.items())),
        "controls": sum(len(s["controls"]) for s in setups),
        "driven_joints": len({j for s in setups for j, _ in driven_chain(s)}),
    }
