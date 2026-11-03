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
#: Jeffy setup types the Unreal builder rebuilds (filled phase by phase, J2-J5)
UNREAL_SUPPORTED_TYPES = set()
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
    section = {"setups": [], "notes": []}
    for setup in setups:
        name = module_name(setup)
        controls = []
        for control in setup["controls"]:
            anchor = _anchor_bone(setup, control)
            snapshot = None
            try:
                snapshot = erm._query_transform_snapshot(
                    control["node"], control.get("key") or "control", name, anchor, anchor)
            except Exception as exc:
                section["notes"].append("{}: no controller snapshot ({})".format(control["name"], exc))
            record = dict(snapshot or {"name": control["name"], "dag_path": control["node"]})
            record.update({"jeffy_key": control.get("key"), "jeffy_space_role": control.get("space_role"),
                           "jeffy_setup": name, "anchor_bone": _short(anchor) if anchor else None})
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
