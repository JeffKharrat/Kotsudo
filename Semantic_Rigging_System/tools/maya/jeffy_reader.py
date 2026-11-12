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
import math

import maya.cmds as cmds

TYPE_ATTR = "jeffySetupType"
KEY_ATTR = "jeffyKey"
ROLE_ATTR = "jeffySpaceRole"
REST_ATTR = "jeffyRest"
OUTPUT_JOINT_ATTR = "jeffyJoint"
MODULE_PREFIX = "Jeffy."
#: setup versions this reader was written against (others are read, and reported)
KNOWN_VERSIONS = {1}
#: Jeffy setup types the Unreal builder rebuilds (J2-J5). Their controls are
#: described as standard Kotsudo controller records (``bone_controllers``:
#: parents, spaces, driven groups); ``wire_attribute`` goes through the
#: blendshape export. The limb, ribbon and spline maths the records cannot
#: carry go in the module's ``params`` (``limb``, ``ribbon``, ``spline``).
UNREAL_SUPPORTED_TYPES = {"fk_chain", "rig_base", "wire_attribute", "ikfk_limb", "ribbon", "spline"}
DESCRIBED_TYPES = ("fk_chain", "rig_base", "ikfk_limb", "ribbon", "spline")
#: IK/FK switch: the ``ikFk`` attribute of a setup's ``settings`` control
#: (1 = IK, 0 = FK; measured on V010: at 0 only the FK controls move the joints)
SWITCH_ATTR = "ikFk"
SWITCH_IK_VALUE, SWITCH_FK_VALUE = 1.0, 0.0
#: Kotsudo roles of the Jeffy control keys that drive no joint directly
#: (the pose check puts the IK ones in IK mode: export_test_poses._IK_ROLES).
KEY_ROLES = {"ik": "ik_effector", "pole": "pole_vector", "settings": "settings",
             "cog": "cog", "hips": "hips", "hipsIk": "ik_control", "chestIk": "ik_control", "head": "head"}
#: Control attributes that have no meaning in Unreal: Maya-only tools
#: (auto-snap runs from a Maya callback) and channel-box separators.
DROPPED_ATTRIBUTES = {"autoSnap"}
#: Maya's RP solver ``twist`` turns the pole plane about the start->end axis
#: (right-handed, measured +30 deg = +30 deg); Unreal's axis convention is
#: mirrored, so the rebuilt rig turns the pole position the other way.
TWIST_SIGN_UNREAL = -1.0


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
        # layers (ribbon...): the setup they ride on
        "base": (_messages(tag, "base") or [None])[0],
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


def modules_config(setups=None, describe=True):
    """Kotsudo modules: one per Jeffy setup that drives bind joints.

    Setups that drive no joint (``wire_attribute``: attribute -> attribute)
    stay in the manifest's ``jeffy`` section only. With ``describe`` the
    limb / ribbon / spline maths the Unreal builder needs is measured from
    the scene into ``params`` (slow on a big rig; the tool's listing skips it).
    """
    setups = read_setups() if setups is None else setups
    index = _Index(setups) if describe else None
    modules = []
    for setup in setups:
        chain = driven_chain(setup)
        if not chain:
            continue
        joints = [joint for joint, _ in chain]
        params = {"jeffy": {"type": setup["type"], "tag": setup["tag"], "version": setup["version"],
                            "options": setup["options"]}}
        params.update(_switch_params(setup, joints, index))
        if describe:
            try:
                params.update(_setup_params(setup, joints, index))
            except Exception as exc:
                params.setdefault("jeffy", {})["describe_error"] = str(exc)
                print("[Jeffy] {}: could not describe the setup for Unreal ({})".format(setup["tag"], exc))
        modules.append({
            "module_type": MODULE_PREFIX + str(setup["type"]),
            "module_name": module_name(setup),
            "chain": joints,
            "chain_items": [{"bone_name": joint, "role": output.get("key") or ""} for joint, output in chain],
            "start_bone": joints[0],
            "end_bone": joints[-1],
            "source": "jeffy",
            "jeffy_tag": setup["tag"],
            "params": params,
        })
    return modules


def _switch_params(setup, joints, index=None):
    """``{"switch": ...}`` (as on tagged rigs) when the setup has an IK/FK
    switch, else ``{}``: the clip export and the pose check use it. With an
    index the switch control's full record and the attribute's description
    come along, so the Unreal builder makes the control itself."""
    for control in setup["controls"]:
        node = control["node"]
        if control.get("key") != "settings" or not cmds.attributeQuery(SWITCH_ATTR, node=node, exists=True):
            continue
        value = float(cmds.getAttr("{}.{}".format(node, SWITCH_ATTR)))
        span = SWITCH_IK_VALUE - SWITCH_FK_VALUE
        record = {"name": control["name"], "role": "settings", "anchor_bone": _short(joints[-1])}
        info = None
        if index is not None:
            record = control_record(setup, control, index)
            info = next((a for a in record.get("attributes") or [] if a.get("name") == SWITCH_ATTR), None)
        switch = {
            "control": record,
            "attribute": SWITCH_ATTR,
            "ik_value": SWITCH_IK_VALUE,
            "fk_value": SWITCH_FK_VALUE,
            "default_ik_weight": round(max(0.0, min(1.0, (value - SWITCH_FK_VALUE) / span)), 4),
        }
        if info:
            switch["attribute_info"] = info
        return {"switch": switch}
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
    that output's joint ('bone_driver'); the others keep their Jeffy key.

    Blended setups (``ikfk_limb``, ``spline`` in chain mode) put a
    blendMatrix between the FK control and the output, so their FK controls
    are matched by key (``fk2`` drives the third joint, in FK mode); the IK
    side keeps Kotsudo's IK roles, which the pose check probes in IK mode."""
    node = _long(control["node"])
    key = control.get("key") or ""
    chain = driven_chain(setup)
    if setup["type"] in ("ikfk_limb", "spline") and key.startswith("fk") and key[2:].isdigit():
        position = int(key[2:])
        if setup["type"] == "ikfk_limb" or setup["options"].get("mode") == "chain":
            if position < len(chain):
                return "bone_driver", chain[position][0]
        return "fk_layer", None
    if setup["type"] == "spline" and key.startswith("ik") and key[2:].isdigit():
        return "ik_control", None
    if setup["type"] == "ribbon":
        return ("bend_joint" if key.startswith("joint") else "bend"), None
    if key in KEY_ROLES:
        return KEY_ROLES[key], None
    for output in setup["outputs"]:
        if output.get("joint") and _source_control(output["node"], index) == node:
            return "bone_driver", output["joint"]
    return key or "control", None


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
    record["parent_space_bone"] = None
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
    elif not parents:
        # A top group that follows one node (Setup.follow: a settings control
        # on its limb's end output) or the setup's own attach anchor.
        target = _follow_target(ancestors, index)
        if target and target.get("bone"):
            record["parent_space_bone"] = target["bone"]
        elif target and target.get("controller"):
            record["parent_controllers"] = [target["controller"]]
            record["parent_controller"] = target["controller"]


def _follow_target(ancestors, index):
    """What the nearest offsetParentMatrix-driven ancestor follows: the node
    whose world matrix feeds its multMatrix (through constant offsets), as a
    space target dict, or None (static groups, or a blend the module handles)."""
    for ancestor in ancestors:
        if ancestor in index.controls:
            return None
        sources = _matrix_sources(ancestor + ".offsetParentMatrix")
        if not sources:
            continue
        mult = sources[0].partition(".")[0]
        if cmds.nodeType(mult) != "multMatrix":
            return None
        for i in cmds.getAttr(mult + ".matrixIn", multiIndices=True) or []:
            for source in _matrix_sources("{}.matrixIn[{}]".format(mult, i)):
                node, _, attribute = source.partition(".")
                if attribute.startswith("worldMatrix"):
                    return _space_target(node, index)
        return None
    return None


def _clean_attributes(record, setup):
    """Drop what Unreal must not rebuild as channels: proxies of another
    control's attribute (the IK/FK switch shown on every limb control),
    channel-box separators (``sep_*``, one-label enums) and Maya-only tools."""
    kept = []
    node = record.get("dag_path") or record.get("name")
    for info in record.get("attributes") or []:
        name = info.get("name") or ""
        if name in DROPPED_ATTRIBUTES or name.startswith("sep_"):
            continue
        if info.get("type") == "enum" and len(info.get("enum_names") or []) <= 1:
            continue
        try:
            if cmds.addAttr("{}.{}".format(node, name), query=True, usedAsProxy=True):
                continue
        except Exception:
            pass
        if name == SWITCH_ATTR and (record.get("jeffy_key") != "settings"):
            continue
        kept.append(info)
    record["attributes"] = kept


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
    _clean_attributes(record, setup)
    if setup["type"] in DESCRIBED_TYPES:
        _describe_hierarchy(record, node, index)
        _mark_module_placed(record, setup)
    return record


WAVE_ATTRIBUTES = ("waveAmplitude", "waveLength", "waveOffset", "waveFalloff")
FROZEN_WAVE_ATTRIBUTE = "waveSpeed"


def transferred_attributes(manifest):
    """{(control, attribute): note} of the control attributes the Jeffy
    modules rebuild in Unreal (limb lengths / softness / stretch / pin /
    twist, spline stretch and wave, ribbon visibility, globalScale). The
    note is None when fully rebuilt, else what differs."""
    handled = {}
    for module in manifest.get("modules") or []:
        module_type = module.get("module_type") or ""
        params = module.get("params") or {}
        limb = params.get("limb") or {}
        if module_type == "Jeffy.ikfk_limb" and limb.get("ik_control"):
            for attribute in (limb.get("attributes") or {}).values():
                if attribute:
                    handled[(limb["ik_control"], attribute)] = None
        spline = params.get("jeffy_spline") or {}
        if module_type == "Jeffy.spline":
            if spline.get("stretch"):
                handled[(spline["stretch"]["control"], spline["stretch"]["attr"])] = None
            wave = spline.get("wave") or {}
            if wave.get("holder"):
                for attribute in WAVE_ATTRIBUTES:
                    handled[(wave["holder"], attribute)] = None
                handled[(wave["holder"], FROZEN_WAVE_ATTRIBUTE)] = (
                    "Control Rig has no absolute-time node: the wave is rebuilt frozen at the exported frame, "
                    "so waveSpeed does not animate it in Unreal")
        ribbon = params.get("ribbon") or {}
        if module_type == "Jeffy.ribbon" and ribbon.get("settings_control") and ribbon.get("visibility_attr"):
            handled[(ribbon["settings_control"], ribbon["visibility_attr"])] = None
    for records in (manifest.get("bone_controllers") or {}).values():
        for record in records:
            if not record.get("jeffy_setup"):
                continue
            for info in record.get("attributes") or []:
                if info.get("name") == "globalScale":
                    handled[(record.get("name"), "globalScale")] = None
    return handled


MATRIX_GROUP_PROBLEM = "placed by a matrix network (offsetParentMatrix)"


def _mark_module_placed(record, setup):
    """Offset groups whose matrix network the Unreal modules rebuild are no
    problem: a ribbon's bend groups follow the strip (Jeffy.ribbon follow
    nulls), a spline chain's FK zeros carry the wave (Jeffy.spline wave
    nulls) and a top group that follows one node is its space bone or
    controller. They become kind ``module`` with what places them."""
    role = record.get("role")
    placed_by = None
    if setup["type"] == "ribbon" and role in ("bend", "bend_joint"):
        placed_by = "Jeffy.ribbon (follows the strip)"
    elif setup["type"] == "spline" and role == "bone_driver":
        placed_by = "Jeffy.spline (wave)"
    follow = record.get("parent_space_bone") if not record.get("parent_space_blend") else None
    first = True
    for entry in record.get("offset_stack") or []:
        if entry.get("kind") != "matrix" or MATRIX_GROUP_PROBLEM not in (entry.get("problems") or []):
            continue
        by = placed_by or ("follows {}".format(follow) if follow and first else None)
        first = False
        if by is None:
            continue
        entry["problems"] = [p for p in entry["problems"] if p != MATRIX_GROUP_PROBLEM]
        if not entry["problems"] and not entry.get("driven"):
            entry["kind"] = "module"
            entry.pop("problems", None)
            entry.pop("driven", None)
        entry["placed_by"] = by


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
    the same index for FK controls (``fk2`` -> ``j2``), the end joint for a
    limb's IK and settings controls, the middle one for its pole, the
    segment's first joint for a ribbon's bend controls, else the first one."""
    chain = driven_chain(setup)
    if not chain:
        return None
    key = control.get("key") or ""
    if key.startswith("fk") and key[2:].isdigit():
        index = int(key[2:])
        if index < len(chain):
            return chain[index][0]
    if setup["type"] == "ikfk_limb":
        if key in ("ik", "settings"):
            return chain[-1][0]
        if key == "pole":
            return chain[min(1, len(chain) - 1)][0]
    if setup["type"] == "ribbon":
        base = _ribbon_base_chain(setup)
        if base:
            if key.startswith("joint") and key[5:].isdigit():
                return base[min(int(key[5:]), len(base) - 1)]
            if key.startswith("bend") and key[4:].isdigit():
                return base[min(int(key[4:]), len(base) - 1)]
    if setup["type"] == "spline" and key == "settings":
        return chain[-1][0]
    return chain[0][0]


def _base_setup(setup):
    """The setup a layer (ribbon) rides on, read from its tag, or None."""
    base = setup.get("base")
    if base and cmds.objExists(base) and cmds.attributeQuery(TYPE_ATTR, node=base, exists=True):
        return read_setup(base)
    return None


def _ribbon_base_chain(setup):
    """Long names of the limb / chain joints a ribbon setup rides on (parent first)."""
    base = _base_setup(setup)
    return [joint for joint, _ in driven_chain(base)] if base else []


# ---------------------------------------------------------------------------
# Setup maths for the Unreal builder (J3-J5): what the controller records
# cannot carry. Everything is MEASURED from the scene (rest matrices, basis
# weights, lengths), the formulas come from Jeffy's own code
# (setups/tools/ikfk_limb.py, ribbon.py, spline.py).
# ---------------------------------------------------------------------------

def _matrix_pose_unreal(matrix):
    """{'position', 'axes'} of a flat Maya world matrix in Unreal axes / cm
    (the form the builder's record_rotation / pose readers take)."""
    import export_rig_manifest as erm

    return {"position": erm._maya_vector_to_unreal(list(matrix[12:15]), apply_unit_scale=False),
            "axes": erm._matrix_axes_unreal(list(matrix))}


def _vector_unreal(vector):
    import export_rig_manifest as erm

    return erm._maya_vector_to_unreal([float(c) for c in vector], apply_unit_scale=False)


def _world_matrix(node):
    return list(cmds.xform(node, query=True, worldSpace=True, matrix=True))


def _mat_mult(a, b):
    """Row-vector (Maya) matrix product a * b of flat 16-value matrices."""
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def _mat_inverse(matrix):
    from maya.api import OpenMaya as om

    return list(om.MMatrix(matrix).inverse())


def _transform_point(point, matrix):
    x, y, z = point
    return [x * matrix[0] + y * matrix[4] + z * matrix[8] + matrix[12],
            x * matrix[1] + y * matrix[5] + z * matrix[9] + matrix[13],
            x * matrix[2] + y * matrix[6] + z * matrix[10] + matrix[14]]


def _v_sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _v_norm(v):
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / length for c in v]


def _v_cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _v_dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def bspline_basis(knots, degree, t, derivative=False):
    """Weights of every control point of a clamped B-spline at param ``t``.

    ``knots`` is Maya's knot vector (outer knots omitted, as ``MFnNurbsSurface``
    returns it); the full vector adds one copy of each end knot. With
    ``derivative`` the first-derivative weights (d/dt) are returned instead.
    This is Cox-de Boor; the Unreal side gets the weights as constants.
    """
    full = [knots[0]] + list(knots) + [knots[-1]]
    count = len(full) - degree - 1              # control points
    high = full[-1]
    t = min(max(t, full[0]), high)
    # The last span is closed on the right.
    span = None
    for i in range(degree, count):
        if full[i] <= t < full[i + 1] or (t >= high and full[i] < full[i + 1] and full[i + 1] >= high):
            span = i
            break
    if span is None:
        span = count - 1
    weights = [[0.0] * count for _ in range(degree + 1)]
    weights[0][span] = 1.0
    for p in range(1, degree + 1):
        for i in range(count):
            left = right = 0.0
            if full[i + p] - full[i] > 1e-12:
                left = (t - full[i]) / (full[i + p] - full[i]) * weights[p - 1][i]
            if i + 1 < count and full[i + p + 1] - full[i + 1] > 1e-12:
                right = (full[i + p + 1] - t) / (full[i + p + 1] - full[i + 1]) * weights[p - 1][i + 1]
            weights[p][i] = left + right
    if not derivative:
        return weights[degree]
    result = [0.0] * count
    p = degree
    for i in range(count):
        left = right = 0.0
        if full[i + p] - full[i] > 1e-12:
            left = p / (full[i + p] - full[i]) * weights[p - 1][i]
        if i + 1 < count and full[i + p + 1] - full[i + 1] > 1e-12:
            right = p / (full[i + p + 1] - full[i + 1]) * weights[p - 1][i + 1]
        result[i] = left - right
    return result


def _surface_info(shape):
    """(knotsU, degreeU, cvs in U, rows in V, domain) of a NURBS strip."""
    from maya.api import OpenMaya as om

    selection = om.MSelectionList()
    selection.add(shape)
    fn = om.MFnNurbsSurface(selection.getDagPath(0))
    return list(fn.knotsInU()), fn.degreeInU, fn.numCVsInU, fn.numCVsInV, fn.knotDomainInU


def _cv_source(shape, cv_index):
    """(matrix plug, local point) of the pointMatrixMult placing a CV."""
    sources = cmds.listConnections("{}.controlPoints[{}]".format(shape, cv_index), source=True,
                                   destination=False) or []
    if not sources or not cmds.nodeType(sources[0]).startswith("pointMatrixMult"):
        raise ValueError("CV {} of {} is not placed by a pointMatrixMult".format(cv_index, _short(shape)))
    node = sources[0]
    matrix = _matrix_sources(node + ".inMatrix")
    if not matrix:
        raise ValueError("{} has no matrix input".format(node))
    return matrix[0], list(cmds.getAttr(node + ".inPoint")[0])


def _matrix_expr(plug, index, depth=0):
    """The world matrix a plug carries, as an expression the Unreal module
    evaluates: a bone (an output), a control, an aimMatrix or a blendMatrix
    of two such expressions (what Jeffy's ribbon builds its columns from)."""
    if depth > 6:
        raise ValueError("matrix expression too deep at {}".format(plug))
    node, _, attribute = plug.partition(".")
    kind = cmds.nodeType(node)
    if attribute.startswith("worldMatrix"):
        target = _space_target(node, index)
        if target.get("bone"):
            return {"kind": "bone", "joint": target["bone"]}
        if target.get("controller"):
            return {"kind": "control", "name": target["controller"]}
        raise ValueError("{} is neither an output nor a control".format(node))
    if kind == "blendMatrix":
        first = _matrix_sources(node + ".inputMatrix")
        second = _matrix_sources(node + ".target[0].targetMatrix")
        if not first or not second:
            raise ValueError("{}: blendMatrix inputs not connected".format(node))
        if _matrix_sources(node + ".target[0].weight"):
            raise ValueError("{}: a driven blend weight is not supported here".format(node))
        return {"kind": "lerp", "a": _matrix_expr(first[0], index, depth + 1),
                "b": _matrix_expr(second[0], index, depth + 1),
                "t": float(cmds.getAttr(node + ".target[0].weight")),
                "translate": float(cmds.getAttr(node + ".target[0].translateWeight")),
                "rotate": float(cmds.getAttr(node + ".target[0].rotateWeight"))}
    if kind == "aimMatrix":
        source = _matrix_sources(node + ".inputMatrix")
        target = _matrix_sources(node + ".primaryTargetMatrix")
        if not source or not target:
            raise ValueError("{}: aimMatrix inputs not connected".format(node))
        if cmds.getAttr(node + ".primaryMode") != 1 or cmds.getAttr(node + ".secondaryMode") != 2:
            raise ValueError("{}: only aim + align aimMatrix modes are supported".format(node))
        up_space = _matrix_sources(node + ".secondaryTargetMatrix")
        expr = {"kind": "aim", "input": _matrix_expr(source[0], index, depth + 1),
                "target": _matrix_expr(target[0], index, depth + 1),
                "aim_axis": _vector_unreal(cmds.getAttr(node + ".primaryInputAxis")[0]),
                "up_axis": _vector_unreal(cmds.getAttr(node + ".secondaryInputAxis")[0]),
                "up_vector": _vector_unreal(cmds.getAttr(node + ".secondaryTargetVector")[0]),
                "up_space": _matrix_expr(up_space[0], index, depth + 1) if up_space else None}
        return expr
    raise ValueError("{} ({}) is not a supported matrix source".format(node, kind))


def _follow_expr(control_node, index):
    """The matrix a control's top group follows (its offsetParentMatrix
    multMatrix: matrix * parentInverse) as {'expr', 'rest'} -- the
    expression the Unreal module evaluates and the group's rest world pose
    (where the module places the null the control hangs from) -- or None."""
    ancestors, current = [], _parent(control_node)
    while current and current not in index.controls:
        ancestors.append(current)
        current = _parent(current)
    for group in ancestors:
        sources = _matrix_sources(group + ".offsetParentMatrix")
        if not sources:
            continue
        mult = sources[0].partition(".")[0]
        if cmds.nodeType(mult) != "multMatrix":
            return None
        for i in cmds.getAttr(mult + ".matrixIn", multiIndices=True) or []:
            for source in _matrix_sources("{}.matrixIn[{}]".format(mult, i)):
                if not source.partition(".")[2].startswith("worldInverseMatrix"):
                    return {"expr": _matrix_expr(source, index), "rest": _matrix_pose_unreal(_world_matrix(group)),
                            "group": _short(group)}
    return None


def _pin_frame(shape, pin, slot, columns_world, cv_halves, knots, degree, u_norm, domain):
    """Which pin-local axes carry the surface tangent (U) and width (V)
    directions, from the pin's rest matrix: {'tangent_axis', 'width_axis'}
    as signed unit axes in Unreal local axes. uvPin's own ``tangentAxis`` /
    ``normalAxis`` settings are not trusted blindly: the fit is checked."""
    t = domain[0] + (domain[1] - domain[0]) * u_norm
    weights = bspline_basis(knots, degree, t)
    d_weights = bspline_basis(knots, degree, t, derivative=True)
    tangent = [sum(w * c[k] for w, c in zip(d_weights, columns_world)) for k in range(3)]
    width = [sum(w * h[k] for w, h in zip(weights, cv_halves)) for k in range(3)]
    tangent = _v_norm(tangent)
    width = _v_norm(_v_sub(width, [_v_dot(width, tangent) * c for c in tangent]))
    basis = [tangent, width, _v_cross(tangent, width)]
    matrix = list(cmds.getAttr("{}.outputMatrix[{}]".format(pin, slot)))
    axes = [_v_norm(matrix[r * 4:r * 4 + 3]) for r in range(3)]
    result = {}
    for label, direction in (("tangent_axis", basis[0]), ("width_axis", basis[1])):
        dots = [_v_dot(direction, axis) for axis in axes]
        j = max(range(3), key=lambda k: abs(dots[k]))
        if abs(dots[j]) < 0.999:
            raise ValueError("{}: the pin frame does not follow the surface ({} fit {:.4f})".format(
                _short(pin), label, abs(dots[j])))
        local = [0.0, 0.0, 0.0]
        local[j] = 1.0 if dots[j] > 0 else -1.0
        result[label] = _vector_unreal(local)
    return result


def _upstream_pin(plug, depth=0):
    """(uvPin node, slot) feeding ``plug`` through multMatrix nodes, or None.
    (An output's offsetParentMatrix <- output_MM <- Offset_MM <- uvPin.)"""
    if depth > 6:
        return None
    for source in _matrix_sources(plug):
        node, _, attribute = source.partition(".")
        if cmds.nodeType(node) == "uvPin" and attribute.startswith("outputMatrix["):
            return node, int(attribute.split("[")[1].rstrip("]"))
        if cmds.nodeType(node) == "multMatrix":
            for i in cmds.getAttr(node + ".matrixIn", multiIndices=True) or []:
                found = _upstream_pin("{}.matrixIn[{}]".format(node, i), depth + 1)
                if found:
                    return found
        if cmds.nodeType(node) == "blendMatrix":      # chain mode: FK/IK blend in front of the pin
            for input_plug in (node + ".target[0].targetMatrix", node + ".inputMatrix"):
                found = _upstream_pin(input_plug, depth + 1)
                if found:
                    return found
    return None


def _limb_params(setup, joints, index):
    """ikfk_limb: the controls by role and the IK goal attributes
    (ikfk_limb._ik_goal); the maths itself lives in the Unreal module."""
    by_key = {c.get("key"): c for c in setup["controls"]}
    ik = by_key.get("ik")
    if ik is None:
        raise ValueError("no IK control")
    ik_node = ik["node"]

    def attr(name):
        return name if cmds.attributeQuery(name, node=ik_node, exists=True) else None

    points = [cmds.xform(j, query=True, worldSpace=True, translation=True) for j in joints]
    options = setup["options"]
    pole = by_key.get("pole")
    return {"limb": {
        "ik_control": ik["name"],
        "pole_control": pole["name"] if pole else None,
        "settings_control": by_key["settings"]["name"] if by_key.get("settings") else None,
        "fk_controls": [by_key[k]["name"] for k in ("fk0", "fk1", "fk2") if k in by_key],
        "upper": math.dist(points[0], points[1]),
        "lower": math.dist(points[1], points[2]),
        "soft": bool(options.get("soft")) and attr("softness") is not None,
        "stretch": bool(options.get("stretch")) and attr("stretch") is not None,
        "pin": bool(options.get("pin")) and pole is not None and attr("pin") is not None,
        "knee": options.get("knee") or "pole",
        "attributes": {"upper_length": attr("upperLength"), "lower_length": attr("lowerLength"),
                       "softness": attr("softness"), "stretch": attr("stretch"), "pin": attr("pin"),
                       "twist": attr("twist") or attr("kneeTwist")},
        "twist_sign_unreal": TWIST_SIGN_UNREAL,
        "default_mode": options.get("default_mode"),
    }}


def _ribbon_params(setup, joints, index):
    """ribbon: per segment the five columns (bone / control / aim / blend
    expressions), the basis weights of each joint on them, the column-local
    width vectors and the pins' rest frames (ribbon.py)."""
    base = _base_setup(setup)
    if base is None:
        raise ValueError("no base setup")
    base_chain = [j for j, _ in driven_chain(base)]
    outputs = {o["key"]: o for o in setup["outputs"]}
    holder = None
    for control in base["controls"]:
        if cmds.attributeQuery("bendyVis", node=control["node"], exists=True):
            holder = control["name"]
    segments = []
    pin_frame = None
    for segment in range(len(base_chain) - 1):
        keys = sorted((k for k in outputs if k.startswith("s{}_".format(segment))), key=lambda k: int(k.split("_")[1]))
        if not keys:
            continue
        first_joint = outputs[keys[0]]["joint"]
        found = _upstream_pin(outputs[keys[0]]["node"] + ".offsetParentMatrix")
        if found is None:
            raise ValueError("segment {}: no uvPin behind {}".format(segment, _short(outputs[keys[0]]["node"])))
        pin = found[0]
        shape = (cmds.listConnections(pin + ".deformedGeometry", source=True, destination=False, shapes=True)
                 or [None])[0]
        knots, degree, cv_count, rows, domain = _surface_info(shape)
        if rows != 2:
            raise ValueError("{}: expected a 2-row strip".format(_short(shape)))
        columns, rests, halves, centers = [], [], [], []
        for column in range(cv_count):
            plug0, local0 = _cv_source(shape, column * rows)
            plug1, local1 = _cv_source(shape, column * rows + 1)
            if plug0 != plug1:
                raise ValueError("{}: column {} rows ride on different matrices".format(_short(shape), column))
            columns.append(_matrix_expr(plug0, index))
            rest = list(cmds.getAttr(plug0))
            rests.append(rest)
            centers.append(_transform_point([(a + b) / 2.0 for a, b in zip(local0, local1)], rest))
            half_local = [(a - b) / 2.0 for a, b in zip(local0, local1)]
            halves.append(half_local)
        halves_world = []
        for rest, half in zip(rests, halves):
            origin = _transform_point([0.0, 0.0, 0.0], rest)
            halves_world.append(_v_sub(_transform_point(half, rest), origin))
        pins_out = []
        for key in keys:
            output = outputs[key]
            found = _upstream_pin(output["node"] + ".offsetParentMatrix")
            if found is None or found[0] != pin:
                raise ValueError("{}: no pin slot".format(_short(output["node"])))
            slot = found[1]
            u = float(cmds.getAttr("{}.coordinate[{}].coordinateU".format(pin, slot)))
            if not cmds.getAttr(pin + ".normalizedIsoParms"):
                u = (u - domain[0]) / (domain[1] - domain[0])
            t = domain[0] + (domain[1] - domain[0]) * u
            frame = _pin_frame(shape, pin, slot, centers, halves_world, knots, degree, u, domain)
            if pin_frame is None:
                pin_frame = frame
            elif frame != pin_frame:
                raise ValueError("{}: pins use different frames".format(_short(pin)))
            pins_out.append({
                "joint": _short(output["joint"]),
                "u": u,
                "weights": bspline_basis(knots, degree, t),
                "d_weights": bspline_basis(knots, degree, t, derivative=True),
                "pin_rest": _matrix_pose_unreal(list(cmds.getAttr("{}.outputMatrix[{}]".format(pin, slot)))),
            })
        segments.append({
            "index": segment,
            "joint": _short(first_joint),
            "columns": columns,
            "column_rest": [_matrix_pose_unreal(r) for r in rests],
            "cv_half": [_vector_unreal(h) for h in halves],        # column-local, Unreal axes
            "pins": pins_out,
        })
    controls = {}
    for control in setup["controls"]:
        follow = _follow_expr(_long(control["node"]), index)
        if follow is not None:
            controls[control["key"]] = dict(follow, name=control["name"])
    return {"ribbon": {
        "base_module": module_name(base),
        "base_joints": [_short(j) for j in base_chain],
        "settings_control": holder,
        "visibility_attr": "bendyVis" if holder else None,
        "pin_frame": pin_frame,
        "segments": segments,
        "controls": controls,
    }}


def _spline_params(setup, joints, index):
    """spline: the IK controls carrying the ribbon's columns, the rest chord
    lengths the hidden spline-IK chain keeps, the pins' rest frames and the
    chain mode's FK controls / wave (spline.py)."""
    options = setup["options"]
    by_key = {c.get("key"): c for c in setup["controls"]}
    outputs = {o["key"]: o for o in setup["outputs"]}
    found = next((_upstream_pin(outputs[k]["node"] + ".offsetParentMatrix") for k in outputs
                  if _upstream_pin(outputs[k]["node"] + ".offsetParentMatrix")), None)
    if found is None:
        raise ValueError("no uvPin behind the outputs")
    pin = found[0]
    shape = (cmds.listConnections(pin + ".deformedGeometry", source=True, destination=False, shapes=True)
             or [None])[0]
    knots, degree, cv_count, rows, domain = _surface_info(shape)
    ik_controls = []
    columns, halves, halves_world, centers = [], [], [], []
    for column in range(cv_count):
        plug0, local0 = _cv_source(shape, column * rows)
        plug1, local1 = _cv_source(shape, column * rows + 1)
        owner = _long(plug0.partition(".")[0])
        if owner not in index.controls:
            raise ValueError("column {} of {} rides on {}, not a control".format(column, _short(shape), owner))
        name = _short(owner)
        if name not in ik_controls:
            ik_controls.append(name)
        rest = list(cmds.getAttr(plug0))
        center_local = [(a + b) / 2.0 for a, b in zip(local0, local1)]
        half_local = [(a - b) / 2.0 for a, b in zip(local0, local1)]
        columns.append({"owner": ik_controls.index(name), "local": _vector_unreal(center_local)})
        centers.append(_transform_point(center_local, rest))
        halves.append(_vector_unreal(half_local))                     # owner-local, Unreal axes
        origin = _transform_point([0.0, 0.0, 0.0], rest)
        halves_world.append(_v_sub(_transform_point(half_local, rest), origin))

    # The hidden length chain: rest chord lengths and the ribbon's rest length.
    chain_joints = []
    for key in sorted(outputs, key=lambda k: int(k[1:]) if k[1:].isdigit() else 999):
        if not key.startswith("j"):
            continue
        output = outputs[key]
        found = _upstream_pin(output["node"] + ".offsetParentMatrix")
        if found is None or found[0] != pin:
            raise ValueError("{}: no pin slot".format(_short(output["node"])))
        slot = found[1]
        u_source = cmds.listConnections("{}.coordinate[{}].coordinateU".format(pin, slot), source=True,
                                        destination=False) or []
        hidden = None
        if u_source:
            closest = cmds.listConnections(u_source[0] + ".input1X", source=True, destination=False) or []
            if closest and cmds.nodeType(closest[0]) == "closestPointOnSurface":
                decompose = cmds.listConnections(closest[0] + ".inPosition", source=True, destination=False) or []
                if decompose:
                    hidden = (cmds.listConnections(decompose[0] + ".inputMatrix", source=True,
                                                   destination=False) or [None])[0]
        if hidden is None:
            raise ValueError("{}: no hidden length joint behind its pin".format(_short(output["node"])))
        chain_joints.append((output, slot, _long(hidden)))
    u_rest = [float(cmds.getAttr("{}.coordinate[{}].coordinateU".format(pin, slot))) for _, slot, _ in chain_joints]
    pins = []
    pin_frame = None
    for (output, slot, hidden), u in zip(chain_joints, u_rest):
        t = domain[0] + (domain[1] - domain[0]) * u
        frame = _pin_frame(shape, pin, slot, centers, halves_world, knots, degree, u, domain)
        pin_frame = pin_frame or frame
        pins.append({"joint": _short(output["joint"]), "u_rest": u,
                     "width_weights": bspline_basis(knots, degree, t),
                     "pin_rest": _matrix_pose_unreal(list(cmds.getAttr("{}.outputMatrix[{}]".format(pin, slot))))})
    hidden_points = [cmds.xform(h, query=True, worldSpace=True, translation=True) for _, _, h in chain_joints]
    bone_lengths = [math.dist(a, b) for a, b in zip(hidden_points, hidden_points[1:])]
    info = next((n for n in cmds.ls(type="curveInfo") if _short(n).startswith(module_name(setup) + "Ribbon")), None)
    if info is None:
        raise ValueError("no curveInfo measuring the ribbon")
    scale_matrix = list(cmds.getAttr(_long(ik_controls[0]) + ".worldMatrix[0]"))
    scale = math.sqrt(sum(c * c for c in scale_matrix[0:3])) or 1.0
    rest_length = float(cmds.getAttr(info + ".arcLength")) / scale

    stretch_holder = next((c for c in setup["controls"] if cmds.attributeQuery("stretch", node=c["node"], exists=True)), None)
    spline = {
        "mode": options.get("mode"),
        "ik_controls": ik_controls,
        "scale_control": ik_controls[0],
        "columns": columns,
        "cv_half": halves,
        "stretch": ({"control": stretch_holder["name"], "attr": "stretch",
                     "value": float(cmds.getAttr(stretch_holder["node"] + ".stretch"))} if stretch_holder else None),
        "rest_length": rest_length,
        "bone_lengths": bone_lengths,
        "pins": pins,
        "pin_frame": pin_frame,
        "fk_controls": [by_key[k]["name"] for k in sorted((k for k in by_key if k.startswith("fk") and k[2:].isdigit()),
                                                           key=lambda k: int(k[2:]))],
        "settings_control": by_key["settings"]["name"] if by_key.get("settings") else None,
    }
    if options.get("mode") == "chain" and options.get("wave") and by_key.get("settings"):
        holder = by_key["settings"]["node"]
        if cmds.attributeQuery("waveAmplitude", node=holder, exists=True):
            count = len(spline["fk_controls"])
            fps = {"game": 15.0, "film": 24.0, "pal": 25.0, "ntsc": 30.0, "show": 48.0, "palf": 50.0,
                   "ntscf": 60.0}.get(cmds.currentUnit(query=True, time=True), 24.0)
            axis_index = {"x": 0, "y": 1, "z": 2}.get(options.get("wave_axis") or "z", 2)
            axis = [0.0, 0.0, 0.0]
            axis[axis_index] = 1.0
            wave_controls = []
            for i, name in enumerate(spline["fk_controls"]):
                node = _long(by_key["fk{}".format(i)]["node"])
                zero, current = None, _parent(node)
                while current and current not in index.controls:
                    if _matrix_sources(current + ".offsetParentMatrix"):
                        zero = current
                    current = _parent(current)
                wave_controls.append({"name": name, "fraction": float(i) / max(count - 1, 1),
                                      "zero": _short(zero) if zero else None,
                                      "zero_rest": _matrix_pose_unreal(_world_matrix(zero)) if zero else None})
            spline["wave"] = {
                "holder": by_key["settings"]["name"],
                "axis_unreal": _vector_unreal(axis),         # the ZRO's own axis the wave turns about
                "fps": fps,
                "time_frames": float(cmds.currentTime(query=True)),
                "controls": wave_controls,
            }
    # ``params.spline`` belongs to Kotsudo's own SplineIK module (schema).
    return {"jeffy_spline": spline}


def _setup_params(setup, joints, index):
    if setup["type"] == "ikfk_limb":
        return _limb_params(setup, joints, index)
    if setup["type"] == "ribbon":
        return _ribbon_params(setup, joints, index)
    if setup["type"] == "spline":
        return _spline_params(setup, joints, index)
    return {}


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
