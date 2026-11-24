"""Retake method M2: an FK layer on the baked clip (docs/animation-export.md,
section 17).

The lightest of the three retake methods. It needs only the baked clip (no
sidecar, no spot method, no Kotsudo rig):

* ``CR_<rig>_FK``: a Control Rig with one FK control per bone, generated from
  the skeleton (``ensure_fk_rig``). Each control sits on its bone and drives
  it (Get control -> Set bone, global space), like the builder's FK module.
* ``LS_<rig>_<clip>_FK``: a Level Sequence whose Control Rig track holds the
  baked clip on those controls (base layer, a key per frame) and an additive
  "Animator" layer on top, as in the layered workflow (M1).

The keys need no solving: a control is its bone, so its value on a frame is
the bone's pose from the bake, read through a rig instance (set the control
where the bone is, read the value back).

FK only: "rotate the forearm 10 degrees more" needs nothing else; an IK-style
edit (plant a hand) has to be made by rotating each bone of the chain.

Why not Unreal's stock pieces: an animation track playing the clip under
Unreal's FK Control Rig plays in the editor but bakes frozen on frame 0 from
Python in 5.6 (SequencerTools.export_anim_sequence), and so does the native
FK Control Rig loaded with the clip; a blueprint Control Rig track bakes
correctly (2026-11-24). The final export and the checks need that bake.
"""

import math

import unreal

from . import anim_import, anim_layers, anim_paths, graph_utils, pose_harness

CONTROL_SUFFIX = "_FKC"
FK_COLOR = (0.2, 0.75, 1.0)


def _log(message):
    unreal.log(f"[FKLayer] {message}")


def _warn(message):
    unreal.log_warning(f"[FKLayer] {message}")


def control_name(bone):
    return f"{graph_utils.sanitize_name(bone)}{CONTROL_SUFFIX}"


# ---------------------------------------------------------------------------
# The FK rig
# ---------------------------------------------------------------------------

def _rig_class(blueprint):
    path = blueprint.get_path_name().split(".")[0]
    return unreal.load_class(None, f"{path}.{path.split('/')[-1]}_C")


def _compile(blueprint):
    if hasattr(blueprint, "recompile_vm"):
        blueprint.recompile_vm()
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    if hasattr(unreal.ControlRigBlueprintLibrary, "request_control_rig_init"):
        unreal.ControlRigBlueprintLibrary.request_control_rig_init(blueprint)


def ensure_fk_rig(mesh, rig_name, rebuild=False):
    """``CR_<rig>_FK`` for ``mesh``: made if missing, its controls and graph
    (re)built if incomplete or ``rebuild`` (in place, so sequences using it
    keep working). Returns (blueprint, {bone: control})."""
    path = anim_paths.fk_rig(rig_name)
    library = unreal.EditorAssetLibrary
    blueprint = library.load_asset(path) if library.does_asset_exist(path) else None
    if blueprint is None:
        made = unreal.ControlRigBlueprintFactory.create_control_rig_from_skeletal_mesh_or_skeleton(mesh, False)
        if made is None:
            raise RuntimeError(f"Could not make a Control Rig from {mesh.get_name()}.")
        made_path = made.get_path_name().split(".")[0]
        library.make_directory(anim_paths.split(path)[0])
        if made_path != path and not library.rename_asset(made_path, path):
            raise RuntimeError(f"Could not move the new FK rig {made_path} to {path}.")
        blueprint = library.load_asset(path)
    bones = [str(k.name) for k in blueprint.hierarchy.get_bones()]
    controls = {bone: control_name(bone) for bone in bones}
    complete = bones and all(
        blueprint.hierarchy.contains(graph_utils.make_key(unreal.RigElementType.CONTROL, c))
        for c in controls.values())
    if complete and not rebuild:
        return blueprint, controls
    return blueprint, _populate(blueprint, path)


def _control_sizes(hierarchy, bone_keys):
    """Shape scale per bone (the shape library is unit-sized, in cm): a third
    of the bone's length to its farthest child, else its parent's size, never
    below 2% of the skeleton's height."""
    position = {str(k.name): graph_utils.get_bone_global_position(hierarchy, str(k.name)) for k in bone_keys}
    children = {}
    for key in bone_keys:
        parent = hierarchy.get_first_parent(key)
        if parent is not None and str(parent.name) in position:
            children.setdefault(str(parent.name), []).append(str(key.name))
    heights = [p.z for p in position.values()] or [0.0]
    floor = max(1.0, 0.02 * (max(heights) - min(heights)))
    sizes = {}
    for key in bone_keys:  # parents first
        bone = str(key.name)
        kids = children.get(bone) or []
        if kids:
            length = max((position[k] - position[bone]).length() for k in kids)
            sizes[bone] = max(floor, length / 3.0)
        else:
            parent = hierarchy.get_first_parent(key)
            sizes[bone] = sizes.get(str(parent.name), floor) if parent is not None else floor
    return sizes


def _populate(blueprint, path):
    """One FK control per bone, and the graph driving each bone from it."""
    hierarchy = blueprint.hierarchy
    hierarchy_controller = blueprint.get_hierarchy_controller()
    # A rig made from a mesh by script has an empty graph and no controller yet.
    controller = (unreal.ControlRigBlueprintLibrary.get_controller(blueprint)
                  or blueprint.get_or_create_controller())
    model = controller.get_graph()
    forwards = graph_utils.find_forwards_solve_node_name(model)
    if not forwards:
        graph_utils.create_unit_node(controller, model, "BeginExecution", unreal.RigUnit_BeginExecution,
                                     unreal.Vector2D(0, 0))
        forwards = graph_utils.find_forwards_solve_node_name(model)
    if not forwards:
        raise RuntimeError("Could not add a Forwards Solve event to the FK rig.")

    bone_keys = list(hierarchy.get_bones())
    sizes = _control_sizes(hierarchy, bone_keys)
    world = graph_utils.invalid_key()
    color = unreal.LinearColor(FK_COLOR[0], FK_COLOR[1], FK_COLOR[2], 1.0)
    controls = {}
    # Parents first (the bone list is in hierarchy order).
    for key in bone_keys:
        bone = str(key.name)
        parent = hierarchy.get_first_parent(key)
        parent_bone = str(parent.name) if parent is not None and str(parent.name) not in ("", "None") else None
        parent_key = (graph_utils.make_key(unreal.RigElementType.CONTROL, controls[parent_bone])
                      if parent_bone in controls else world)
        name = control_name(bone)
        graph_utils.create_control(
            hierarchy, hierarchy_controller, parent_key, name, None, color, (sizes[bone],) * 3,
            shape_name="Circle_Thin", global_transform=graph_utils.get_bone_global_transform(hierarchy, bone),
            display_name=bone)
        controls[bone] = name

    previous = forwards
    for index, key in enumerate(bone_keys):
        bone = str(key.name)
        safe = graph_utils.sanitize_name(bone)
        get_node, set_node = f"FK_{safe}_Get", f"FK_{safe}_Set"
        graph_utils.create_transform_getter(controller, model, get_node,
                                            unreal.Vector2D(400, 200 + index * 220), controls[bone])
        graph_utils.create_unit_node(controller, model, set_node, unreal.RigUnit_SetTransform,
                                     unreal.Vector2D(900, 200 + index * 220))
        graph_utils.set_key_pin(controller, model, set_node, ["Item"], "Bone", bone)
        graph_utils.set_any_pin(controller, model, set_node, ["Space"], "GlobalSpace")
        graph_utils.set_any_pin(controller, model, set_node, ["bInitial", "Initial"], "False")
        graph_utils.set_any_pin(controller, model, set_node, ["Weight"], "1.0")
        graph_utils.set_any_pin(controller, model, set_node, ["bPropagateToChildren"], "True")
        graph_utils.connect_pins(controller, model, f"{get_node}.Transform", f"{set_node}.Value")
        graph_utils.connect_exec(controller, model, previous, set_node)
        previous = set_node

    _compile(blueprint)
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, False)
    _log(f"Made {path}: {len(controls)} FK control(s), one per bone.")
    return controls


# ---------------------------------------------------------------------------
# Keys from the bake
# ---------------------------------------------------------------------------

def _component_poses(anim, bones, count):
    """{bone: [unreal.Transform per frame]} in component space."""
    options = unreal.AnimPoseEvaluationOptions()
    space = unreal.AnimPoseSpaces.WORLD
    poses = {bone: [] for bone in bones}
    for index in range(count):
        pose = unreal.AnimPoseExtensions.get_anim_pose_at_frame(anim, index, options)
        for bone in bones:
            poses[bone].append(unreal.AnimPoseExtensions.get_bone_pose(pose, bone, space))
    return poses


def compute_fk_values(blueprint, controls, baked):
    """Per frame, every FK control's value that puts its bone where the bake
    has it. Same shape as anim_layers.compute_control_values."""
    count = unreal.AnimationLibrary.get_num_keys(baked)
    instance = pose_harness.RigInstance(blueprint)
    hierarchy = instance.hierarchy
    order = [bone for bone in controls if instance.key("CONTROL", controls[bone]) is not None]
    keys = {bone: instance.key("CONTROL", controls[bone]) for bone in order}
    poses = _component_poses(baked, order, count)
    transforms = {controls[bone]: [] for bone in order}
    previous = {bone: (0.0, 0.0, 0.0) for bone in order}
    for index in range(count):
        instance.reset()
        for bone in order:  # parents first
            instance.set_global(keys[bone], poses[bone][index])
        for bone in order:
            value = hierarchy.get_control_value(keys[bone], unreal.RigControlValueType.CURRENT)
            euler = unreal.RigHierarchy.get_euler_transform_from_control_value(value)
            rotation = euler.rotation
            rotator = anim_layers.continuous_rotator(rotation.roll, rotation.pitch, rotation.yaw, previous[bone])
            previous[bone] = rotator
            location, scale = euler.location, euler.scale
            transforms[controls[bone]].append(
                ((location.x, location.y, location.z), rotator, (scale.x, scale.y, scale.z)))
    instance.reset()
    return {"transforms": transforms, "floats": {}, "missing": [b for b in controls if b not in keys],
            "frames": count}


# ---------------------------------------------------------------------------
# Check against the bake (no sidecar needed)
# ---------------------------------------------------------------------------

def compare_with_bake(anim, baked, frames=None):
    """Worst distance between two Anim Sequences of the same skeleton, per
    bone over the frames, in component space. Returns a small report."""
    count = min(unreal.AnimationLibrary.get_num_keys(baked), unreal.AnimationLibrary.get_num_keys(anim))
    frames = list(range(count)) if frames is None else [f for f in frames if f < count]
    pose0 = unreal.AnimPoseExtensions.get_anim_pose_at_frame(baked, 0, unreal.AnimPoseEvaluationOptions())
    bones = [str(b) for b in unreal.AnimPoseExtensions.get_bone_names(pose0)]
    a = _component_poses(anim, bones, count)
    b = _component_poses(baked, bones, count)
    worst = {}
    for bone in bones:
        wp = wr = 0.0
        wf = None
        for index in frames:
            ta, tb = a[bone][index], b[bone][index]
            p = math.dist((ta.translation.x, ta.translation.y, ta.translation.z),
                          (tb.translation.x, tb.translation.y, tb.translation.z))
            qa, qb = ta.rotation, tb.rotation
            r = anim_import._q_angle((qa.x, qa.y, qa.z, qa.w), (qb.x, qb.y, qb.z, qb.w))
            if p > wp:
                wp, wf = p, index
            wr = max(wr, r)
        worst[bone] = (wp, wr, wf)
    wp = max((v[0] for v in worst.values()), default=0.0)
    wr = max((v[1] for v in worst.values()), default=0.0)
    top = sorted(worst.items(), key=lambda item: -item[1][0])[:5]
    return {"position_cm": round(wp, 5), "rotation_deg": round(wr, 5),
            "worst_bones": [(n, round(p, 5), round(r, 5), f) for n, (p, r, f) in top],
            "bones": len(bones), "frames": len(frames)}


# ---------------------------------------------------------------------------
# One clip
# ---------------------------------------------------------------------------

def build_fk_clip(baked, mesh, skeleton, rig_name, clip, start=0, add_layer=True, check=True,
                  rebuild_rig=False):
    """Everything for one clip. Returns {"sequence", "stats", "check", "layer_job"}."""
    blueprint, controls = ensure_fk_rig(mesh, rig_name, rebuild=rebuild_rig)
    values = compute_fk_values(blueprint, controls, baked)
    count = values["frames"]
    length = unreal.AnimationLibrary.get_sequence_length(baked)
    fps = round((count - 1) / length, 3) if length > 0 else 30.0
    frames = [start + i for i in range(count)]
    sequence, binding = anim_layers.make_sequence(anim_paths.sequence(rig_name, clip, "fk_layer"), mesh, fps,
                                                  frames[0], frames[-1], actor_label=f"{rig_name}_Anim")
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    track = unreal.ControlRigSequencerLibrary.find_or_create_control_rig_track(
        world, sequence, _rig_class(blueprint), binding, False)
    if track is None or not track.get_sections():
        raise RuntimeError("Could not add the FK Control Rig track.")
    stats = anim_layers.write_base_keys(track.get_sections()[0], values, frames)
    _log(f"Clip '{clip}': FK base layer keyed on {stats['channels']} channel(s), {stats['animated']} animated, "
         f"{stats['keys']} key(s), {count} frame(s).")
    if stats["not_found"]:
        _warn(f"{len(stats['not_found'])} channel(s) not on the track: {stats['not_found'][:8]}")
    unreal.EditorAssetLibrary.save_loaded_asset(sequence, False)

    report = None
    if check:
        played = anim_layers.export_to_anim_sequence(
            sequence, binding, skeleton, mesh, f"{anim_paths.temp_folder(rig_name)}/A_{rig_name}_{clip}_FK_Check",
            template=baked)
        report = compare_with_bake(played, baked)
        _log(f"Clip '{clip}': FK base layer vs the bake: {report['position_cm']} cm / "
             f"{report['rotation_deg']} deg (worst {report['worst_bones'][:2]}).")
        unreal.EditorAssetLibrary.delete_directory(anim_paths.temp_folder(rig_name))
    layer_job = (sequence, list(values["transforms"])) if add_layer else None
    return {"sequence": sequence, "stats": stats, "check": report, "layer_job": layer_job}
