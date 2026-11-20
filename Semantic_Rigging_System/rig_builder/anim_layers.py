"""A Maya clip as the locked base layer of a Control Rig track, with an empty
animator layer on top (see docs/animation-export.md, "Layered workflow").

The clip's sidecar (``<base>.<clip>.anim.json``) holds every animator
control's world state and channel values on every frame. For each frame the
pose-check rule turns those into Unreal control targets (Unreal rest x Maya
world delta), the rig is solved (set -> solve -> set again, twice, so
controls under rig-driven spaces land right), and each control's own value is
read back: the number Sequencer keys. Nothing is solved backwards: Maya
already says where every control is.

The values are written straight into the base section's channels:

* transform controls: Location / Rotation / Scale X Y Z. Rotation channels
  are roll, pitch, yaw (the controls do not use a preferred rotation order),
  kept continuous from frame to frame (no 360 degree jumps, no Euler flips);
* animation channels (ikFk, space, sliders): the raw Maya value, as the pose
  check sets them. Sequencer names them like the rig element (ikFk, ikFk_2).

A channel that never changes gets one key; the others get a key on every
frame, linear between frames (like a bake). The base section is then locked
and an additive layer is added for the animator.

The result is checked end to end: the Level Sequence is exported to an Anim
Sequence by Unreal's own evaluation, and every bone on every frame is
compared with the Maya joints in the sidecar (anim_import.check_anim_sequence).
"""

import math

import unreal

from . import anim_import, pose_harness

SEQUENCE_FOLDER = "/Game/RigBuilder/Anims"
ANIMATOR_LAYER_NAME = "Animator"
# Below this a channel counts as constant (cm, degrees or plain value). The
# solve runs in 32-bit floats, so a still control reads back with ~1e-4 noise.
CONSTANT_TOLERANCE = 1e-3
_AXES = ("X", "Y", "Z")
# Set -> solve passes per frame: repeated until every control is within the
# tolerance of its target (4 on Murakami V010).
MAX_SOLVE_PASSES = 12
SETTLE_TOLERANCE_CM = 0.01


def _distance(a, b):
    return math.dist((a.translation.x, a.translation.y, a.translation.z),
                     (b.translation.x, b.translation.y, b.translation.z))


def _log(message):
    unreal.log(f"[AnimLayers] {message}")


def _warn(message):
    unreal.log_warning(f"[AnimLayers] {message}")


# ---------------------------------------------------------------------------
# Euler continuity
# ---------------------------------------------------------------------------

def _unwrap(angle, reference):
    """``angle`` plus the multiple of 360 that brings it closest to ``reference``."""
    return angle + 360.0 * round((reference - angle) / 360.0)


def continuous_rotator(roll, pitch, yaw, previous):
    """(roll, pitch, yaw) closest to ``previous`` among the forms of the same
    rotation: each angle +- 360, and the flipped form (pitch -> 180 - pitch,
    roll and yaw + 180), which a quaternion -> rotator conversion can switch
    to from one frame to the next."""
    best = None
    for r, p, y in ((roll, pitch, yaw), (roll + 180.0, 180.0 - pitch, yaw + 180.0)):
        candidate = (_unwrap(r, previous[0]), _unwrap(p, previous[1]), _unwrap(y, previous[2]))
        distance = sum(abs(candidate[i] - previous[i]) for i in range(3))
        if best is None or distance < best[0]:
            best = (distance, candidate)
    return best[1]


# ---------------------------------------------------------------------------
# Control values per frame (the pose-check rule, frame by frame)
# ---------------------------------------------------------------------------

def _depth(hierarchy, key):
    depth = 0
    parent = hierarchy.get_first_parent(key)
    while parent is not None and str(parent.name) not in ("", "None"):
        depth += 1
        parent = hierarchy.get_first_parent(parent)
    return depth


def compute_control_values(rig_blueprint, document):
    """Per frame, every animator control's value in Unreal.

    Returns ``{"transforms": {control: [(location, rotator, scale), ...]},
    "floats": {channel element name: [value, ...]}, "missing": [...]}``.
    Rotators are (roll, pitch, yaw), already continuous over the clip.
    """
    instance = pose_harness.RigInstance(rig_blueprint)
    hierarchy = instance.hierarchy
    controls = document.get("controls") or {}
    rest = document.get("control_rest") or {}
    frames = document["time"]["frames"]

    instance.reset()
    for _ in range(3):
        instance.run()
    keys, missing = {}, []
    for name in controls:
        key = instance.key("CONTROL", name)
        if key is None:
            missing.append(name)
        else:
            keys[name] = key
    # Parents first: setting a control moves its children with it.
    order = sorted(keys, key=lambda n: _depth(hierarchy, keys[n]))
    ue_rest = {name: instance.global_transform(keys[name], True) for name in order}

    # Animation channels (custom attributes) of each control, by element.
    float_plan = []
    for name in order:
        for attribute, record in (controls[name].get("channels") or {}).items():
            if attribute.startswith(("translate", "rotate", "scale")):
                continue
            channel = instance.channel_key(keys[name], attribute)
            if channel is None:
                missing.append(f"{name}.{attribute}")
                continue
            float_plan.append((str(channel.name), channel, record.get("values") or []))

    transforms = {name: [] for name in order}
    floats = {element: [] for element, _, _ in float_plan}
    previous = {name: (0.0, 0.0, 0.0) for name in order}
    worst_passes, unsettled = 0, []
    for index in range(len(frames)):
        instance.reset()
        for _, channel, values in float_plan:
            instance.set_float(channel, values[index])
        targets = [(keys[name], pose_harness._posed_control(
            ue_rest[name], rest.get(name),
            {"t": controls[name]["samples"][index][0], "q": controls[name]["samples"][index][1]}))
            for name in order]
        # Set -> solve until every control stays on its target: a control in a
        # space the rig computes (follow / space nulls) only lands once the
        # controls that space follows have, so deep chains (eyelids under
        # the head under the neck spline) need ~4 passes.
        for passes in range(1, MAX_SOLVE_PASSES + 1):
            for key, target in targets:
                instance.set_global(key, target)
            instance.run()
            off = max(_distance(hierarchy.get_global_transform(key, False), target) for key, target in targets)
            if off <= SETTLE_TOLERANCE_CM:
                break
        worst_passes = max(worst_passes, passes)
        if off > SETTLE_TOLERANCE_CM:
            unsettled.append((frames[index], round(off, 4)))

        for name in order:
            value = hierarchy.get_control_value(keys[name], unreal.RigControlValueType.CURRENT)
            euler = unreal.RigHierarchy.get_euler_transform_from_control_value(value)
            rotation = euler.rotation
            rotator = continuous_rotator(rotation.roll, rotation.pitch, rotation.yaw, previous[name])
            previous[name] = rotator
            location, scale = euler.location, euler.scale
            transforms[name].append(((location.x, location.y, location.z), rotator, (scale.x, scale.y, scale.z)))
        for element, channel, _ in float_plan:
            value = hierarchy.get_control_value(channel, unreal.RigControlValueType.CURRENT)
            floats[element].append(unreal.RigHierarchy.get_float_from_control_value(value))
    instance.reset()
    _log(f"Controls placed on every frame in at most {worst_passes} solve pass(es).")
    if unsettled:
        _warn(f"{len(unsettled)} frame(s) where a control stayed off its Maya target after "
              f"{MAX_SOLVE_PASSES} passes (frame, cm): {unsettled[:5]}")
    return {"transforms": transforms, "floats": floats, "missing": missing,
            "solve_passes": worst_passes, "unsettled": unsettled}


# ---------------------------------------------------------------------------
# Level Sequence
# ---------------------------------------------------------------------------

def _delete_if_exists(path):
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        if not unreal.EditorAssetLibrary.delete_asset(path):
            raise RuntimeError(f"Could not delete {path} (open in an editor?). Close it and run again.")


def _level_actor(mesh, label):
    """The Skeletal Mesh actor labelled ``label`` in the open level (made at
    the origin with ``mesh`` if there is none)."""
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in actors.get_all_level_actors():
        if actor.get_actor_label() == label and isinstance(actor, unreal.SkeletalMeshActor):
            return actor
    actor = actors.spawn_actor_from_object(mesh, unreal.Vector(0.0, 0.0, 0.0), unreal.Rotator(0.0, 0.0, 0.0))
    if actor is None:
        raise RuntimeError(f"Could not place {mesh.get_name()} in the level.")
    actor.set_actor_label(label)
    _log(f"Placed '{label}' in the level for the sequences (save the level to keep it).")
    return actor


def create_sequence(name, mesh, rig_class, document, actor_label, folder=SEQUENCE_FOLDER):
    """A new Level Sequence bound to the level actor ``actor_label`` (showing
    ``mesh``) with a Control Rig track of ``rig_class``.
    Returns (sequence, binding, track)."""
    unreal.LevelSequenceEditorBlueprintLibrary.close_level_sequence()
    path = f"{folder}/{name}"
    sequence = unreal.EditorAssetLibrary.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if isinstance(sequence, unreal.LevelSequence):
        # Rebuilt in place (deleting fails while anything still references it).
        for old in list(sequence.get_bindings()):
            old.remove()
        for old in list(sequence.get_tracks()):
            sequence.remove_track(old)
    else:
        if sequence is not None:
            _delete_if_exists(path)
        sequence = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, folder, unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    timing = document["time"]
    fps = float(timing["fps"])
    if abs(fps - round(fps)) > 1e-6:
        raise RuntimeError(f"Frame rate {fps} is not a whole number: not supported yet.")
    sequence.set_display_rate(unreal.FrameRate(int(round(fps)), 1))
    start, end = int(round(timing["start"])), int(round(timing["end"]))
    sequence.set_playback_start(start)
    sequence.set_playback_end(end + 1)  # end is exclusive in Unreal
    # A possessable (an actor in the level), not a spawnable: Unreal 5.6's
    # scripted spawnables are new-style bindings that the Anim Sequence
    # export never spawns ("No skel mesh found in Sequencer").
    binding = sequence.add_possessable(_level_actor(mesh, actor_label))
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    track = unreal.ControlRigSequencerLibrary.find_or_create_control_rig_track(world, sequence, rig_class, binding, False)
    if track is None:
        raise RuntimeError("Could not add a Control Rig track for the rig.")
    return sequence, binding, track


def _is_constant(values):
    return max(values) - min(values) <= CONSTANT_TOLERANCE


def write_base_keys(section, values, frames):
    """Key the computed values into ``section``'s channels. Returns counts."""
    channels = {str(c.channel_name): c for c in section.get_all_channels()}
    frame_numbers = [unreal.FrameNumber(int(round(f))) for f in frames]
    linear = unreal.MovieSceneKeyInterpolation.LINEAR
    display = unreal.MovieSceneTimeUnit.DISPLAY_RATE
    stats = {"channels": 0, "animated": 0, "keys": 0, "not_found": []}

    def key(name, series):
        channel = channels.get(name)
        if channel is None:
            stats["not_found"].append(name)
            return
        for existing in list(channel.get_keys()):
            channel.remove_key(existing)
        stats["channels"] += 1
        if _is_constant(series):
            channel.add_key(frame_numbers[0], float(series[0]), 0.0, display, linear)
            stats["keys"] += 1
            return
        stats["animated"] += 1
        for frame, value in zip(frame_numbers, series):
            channel.add_key(frame, float(value), 0.0, display, linear)
        stats["keys"] += len(series)

    for control, samples in values["transforms"].items():
        for part, slot in (("Location", 0), ("Rotation", 1), ("Scale", 2)):
            for axis_index, axis in enumerate(_AXES):
                key(f"{control}.{part}.{axis}", [sample[slot][axis_index] for sample in samples])
    for element, series in values["floats"].items():
        key(element, series)
    return stats


def add_animator_layer(sequence, control_rig, controls, name=ANIMATOR_LAYER_NAME):
    """Open the sequence, add an additive anim layer holding the animator
    controls, lock the base layer. Returns the layer object or None."""
    library = unreal.ControlRigSequencerLibrary
    unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(sequence)
    # A sequence rebuilt in place keeps the layer list of the previous run.
    for old_index in range(len(library.get_anim_layers() or []) - 1, 0, -1):
        try:
            library.delete_anim_layer(old_index)
        except Exception:
            pass
    control_rig.clear_control_selection()
    for control in controls:
        control_rig.select_control(control, True)
    index = library.add_anim_layer_from_selection()
    control_rig.clear_control_selection()
    layers = list(library.get_anim_layers() or [])
    if index is None or index < 0 or index >= len(layers):
        _warn("Unreal did not add the animator layer (add it from the Anim Layers panel).")
        return None
    layer = layers[index]
    for setter, value in (("set_name", name), ("set_lock", False)):
        if hasattr(layer, setter):
            try:
                getattr(layer, setter)(value)
            except Exception:
                pass
    base = layers[0] if index != 0 else None
    if base is not None and hasattr(base, "set_lock"):
        try:
            base.set_lock(True)
        except Exception as exc:
            _warn(f"Could not lock the base layer: {exc}")
    return layer


# ---------------------------------------------------------------------------
# End-to-end check
# ---------------------------------------------------------------------------

def export_to_anim_sequence(sequence, binding, skeleton, mesh, name, folder=SEQUENCE_FOLDER, template=None):
    """Bake the Level Sequence to an Anim Sequence with Unreal's own
    evaluation (what the animator sees). Returns the Anim Sequence."""
    _delete_if_exists(f"{folder}/{name}")
    rate = sequence.get_display_rate()
    anim = None
    if template is not None:
        # An Anim Sequence already at the clip's rate (the imported baked
        # clip): a new one is 30 fps and refuses 24 (not a multiple or factor).
        anim = unreal.EditorAssetLibrary.duplicate_loaded_asset(template, f"{folder}/{name}")
    if anim is None:
        factory = unreal.AnimSequenceFactory()
        factory.set_editor_property("target_skeleton", skeleton)
        try:
            factory.set_editor_property("preview_skeletal_mesh", mesh)
        except Exception:
            pass
        anim = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.AnimSequence, factory)
        controller = anim.get_editor_property("controller")
        try:
            controller.set_number_of_frames(unreal.FrameNumber(0), False)
            controller.set_frame_rate(rate, False)
        except Exception as exc:
            _warn(f"Could not set the Anim Sequence frame rate: {exc}")
    options = unreal.AnimSeqExportOption()
    options.set_editor_property("use_custom_frame_rate", True)
    options.set_editor_property("custom_frame_rate", rate)
    options.set_editor_property("export_transforms", True)
    options.set_editor_property("export_morph_targets", True)
    options.set_editor_property("record_in_world_space", False)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    ok = unreal.SequencerTools.export_anim_sequence(world, sequence, anim, options, binding, False)
    if not ok:
        raise RuntimeError("Exporting the Level Sequence to an Anim Sequence failed.")
    return anim


def build_layered_clip(rig_blueprint, rig_class, mesh, skeleton, document, manifest=None,
                       report_path=None, add_layer=True, check=True, baked_anim=None):
    """Everything for one clip. Returns {"sequence", "stats", "report"}.
    ``baked_anim``: the clip's imported baked Anim Sequence (route A), used
    as the template of the check's Anim Sequence (same frame rate)."""
    clip = document.get("clip") or "clip"
    rig_name = document.get("rig_name") or "rig"
    frames = document["time"]["frames"]
    if any(abs(f - round(f)) > 1e-6 for f in frames):
        raise RuntimeError("Sub-frame samples are not supported yet (export the clip with step 1).")

    values = compute_control_values(rig_blueprint, document)
    if values["missing"]:
        _warn(f"{len(values['missing'])} control(s)/channel(s) of the clip are not in the rig: "
              f"{', '.join(values['missing'][:8])}")

    sequence, binding, track = create_sequence(f"LS_{rig_name}_{clip}", mesh, rig_class, document,
                                               actor_label=f"{rig_name}_Anim")
    sections = track.get_sections()
    if not sections:
        raise RuntimeError("The Control Rig track has no section.")
    base = sections[0]
    stats = write_base_keys(base, values, frames)
    _log(f"Clip '{clip}': base layer keyed on {stats['channels']} channel(s), {stats['animated']} animated, "
         f"{stats['keys']} key(s), {len(frames)} frame(s).")
    if stats["not_found"]:
        _warn(f"{len(stats['not_found'])} channel(s) not on the track: {stats['not_found'][:8]}")

    layer = None
    if add_layer:
        proxies = unreal.ControlRigSequencerLibrary.get_control_rigs(sequence)
        control_rig = proxies[0].control_rig if proxies else None
        if control_rig is None:
            _warn("No Control Rig on the sequence's track: animator layer not added.")
        else:
            layer = add_animator_layer(sequence, control_rig, list(values["transforms"]))
            if layer is not None:
                _log(f"Animator layer added (additive, weight 1); base layer locked.")
    unreal.EditorAssetLibrary.save_loaded_asset(sequence, False)

    report = None
    if check:
        anim = export_to_anim_sequence(sequence, binding, skeleton, mesh, f"A_{rig_name}_{clip}_ControlRig",
                                       template=baked_anim)
        report = anim_import.check_anim_sequence(anim, document, manifest, report_path,
                                                 label=f"{clip} on the Control Rig (base layer)")
    return {"sequence": sequence, "stats": stats, "report": report, "layer": layer}
