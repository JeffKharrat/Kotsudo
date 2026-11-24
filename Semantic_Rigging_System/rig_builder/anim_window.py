"""Retake method M3: the retake window (docs/animation-export.md, section 17).

The baked clip plays everywhere; the Kotsudo rig takes over only on the
frames the animator wants to retake, with a short ease in and out.

``LS_<rig>_<clip>_Window`` holds two Control Rig tracks on the same binding:

1. the generated FK rig (``CR_<rig>_FK``, as in M2) holding the bake on every
   frame: the clip, exact to 0.01 cm, needing no Maya data;
2. the Kotsudo rig, keyed only on the window plus the ease frames from
   Maya's control spots (the M1 machinery on those frames only), its section
   eased in and out so it blends from the bake into the rig and back. The
   Animator layer sits on this track, over the same frames.

Outside the window the result is the bake; inside it, the Kotsudo rig with
IK, spaces and switches, as in Maya. The edge jumps are what the ease has to
hide: the rig pose differs from the bake by the rig's own error (up to
0.5 cm on V010's arm ribbons).

Why the bake is on an FK rig and not an animation track: an animation track
plays in the editor but Unreal's scripted bake of it is frozen on frame 0 in
5.6 (section 17), and the checks need that bake.
"""

import copy

import unreal

from . import anim_fk_layer, anim_layers, anim_paths

DEFAULT_EASE_FRAMES = 3


def _log(message):
    unreal.log(f"[RetakeWindow] {message}")


def _warn(message):
    unreal.log_warning(f"[RetakeWindow] {message}")


def slice_document(document, first_index, last_index):
    """The clip's sidecar limited to frames ``first_index``..``last_index``
    (indices into ``time.frames``)."""
    sub = copy.copy(document)
    timing = dict(document["time"])
    frames = list(timing["frames"])[first_index:last_index + 1]
    timing.update(frames=frames, start=frames[0], end=frames[-1])
    sub["time"] = timing
    controls = {}
    for name, control in (document.get("controls") or {}).items():
        control = dict(control)
        control["samples"] = list(control.get("samples") or [])[first_index:last_index + 1]
        channels = {}
        for attribute, record in (control.get("channels") or {}).items():
            record = dict(record)
            if record.get("values") is not None:
                record["values"] = list(record["values"])[first_index:last_index + 1]
            channels[attribute] = record
        control["channels"] = channels
        controls[name] = control
    sub["controls"] = controls
    return sub


def set_section_ease(section, sequence, ease_in_frames, ease_out_frames):
    """Manual ease in/out on ``section``, in display frames."""
    tick = sequence.get_tick_resolution()
    rate = sequence.get_display_rate()
    ticks_per_frame = (tick.numerator / tick.denominator) / (rate.numerator / rate.denominator)
    easing = section.get_editor_property("easing")
    easing.import_text(
        f"(bManualEaseIn={bool(ease_in_frames)},ManualEaseInDuration={int(round(ease_in_frames * ticks_per_frame))},"
        f"bManualEaseOut={bool(ease_out_frames)},ManualEaseOutDuration={int(round(ease_out_frames * ticks_per_frame))})")
    section.set_editor_property("easing", easing)


def window_range(frames, window, ease):
    """(first index, last index, ease in frames, ease out frames) of the
    window plus its ease frames, clamped to the clip."""
    start, end = int(window[0]), int(window[1])
    if end < start:
        start, end = end, start
    first_frame, last_frame = int(round(frames[0])), int(round(frames[-1]))
    lo = max(first_frame, start - ease)
    hi = min(last_frame, end + ease)
    if lo > last_frame or hi < first_frame:
        raise RuntimeError(f"Window {start}-{end} is outside the clip ({first_frame}-{last_frame}).")
    index = {int(round(f)): i for i, f in enumerate(frames)}
    return index[lo], index[hi], max(0, min(start, last_frame) - lo), max(0, hi - max(end, first_frame))


def rig_tracks(sequence, rig_name):
    """(FK track, Kotsudo track) of a window sequence (either may be None)."""
    fk_class = anim_paths.split(anim_paths.fk_rig(rig_name))[1] + "_C"
    fk_track = rig_track = None
    for proxy in unreal.ControlRigSequencerLibrary.get_control_rigs(sequence) or []:
        if proxy.control_rig.get_class().get_name() == fk_class:
            fk_track = proxy.track
        else:
            rig_track = proxy.track
    return fk_track, rig_track


def build_window_clip(rig_blueprint, rig_class, mesh, skeleton, document, baked, window,
                      ease=DEFAULT_EASE_FRAMES, add_layer=True, check=True):
    """Everything for one clip. Returns {"sequence", "check", "layer_job", ...}."""
    clip = document.get("clip") or "clip"
    rig_name = document.get("rig_name") or mesh.get_name()
    frames = [int(round(f)) for f in document["time"]["frames"]]

    # 1. The bake, on the FK rig, every frame.
    fk_blueprint, fk_controls = anim_fk_layer.ensure_fk_rig(mesh, rig_name)
    fk_values = anim_fk_layer.compute_fk_values(fk_blueprint, fk_controls, baked)
    if fk_values["frames"] != len(frames):
        _warn(f"The baked clip has {fk_values['frames']} frame(s), the sidecar {len(frames)}: "
              "using the shorter.")
    count = min(fk_values["frames"], len(frames))
    frames = frames[:count]

    path = anim_paths.sequence(rig_name, clip, "window")
    sequence, binding = anim_layers.make_sequence(path, mesh, document["time"]["fps"], frames[0], frames[-1],
                                                  actor_label=f"{rig_name}_Anim")
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    library = unreal.ControlRigSequencerLibrary
    fk_track = library.find_or_create_control_rig_track(world, sequence, anim_fk_layer._rig_class(fk_blueprint),
                                                        binding, False)
    fk_stats = anim_layers.write_base_keys(fk_track.get_sections()[0], fk_values, frames)

    # 2. The Kotsudo rig on the window only.
    first, last, ease_in, ease_out = window_range(frames, window, ease)
    sub = slice_document(document, first, last)
    values = anim_layers.compute_control_values(rig_blueprint, sub)
    if values["missing"]:
        _warn(f"{len(values['missing'])} control(s)/channel(s) of the clip are not in the rig: "
              f"{', '.join(values['missing'][:8])}")
    rig_track = library.find_or_create_control_rig_track(world, sequence, rig_class, binding, False)
    if rig_track is None or rig_track == fk_track:
        raise RuntimeError("Could not add the Kotsudo rig's track next to the FK one.")
    section = rig_track.get_sections()[0]
    section.set_range(frames[first], frames[last] + 1)
    stats = anim_layers.write_base_keys(section, values, sub["time"]["frames"])
    set_section_ease(section, sequence, ease_in, ease_out)
    unreal.EditorAssetLibrary.save_loaded_asset(sequence, False)
    _log(f"Clip '{clip}': bake on the FK rig ({fk_stats['keys']} keys), Kotsudo rig on frames "
         f"{frames[first]}-{frames[last]} (window {window[0]}-{window[1]}, ease {ease_in}/{ease_out}), "
         f"{stats['keys']} key(s).")

    report = None
    if check:
        report = check_window(sequence, binding, skeleton, mesh, baked, rig_name, clip, frames,
                              (frames[first] + ease_in, frames[last] - ease_out), (frames[first], frames[last]))
    layer_job = None
    if add_layer:
        layer_job = {"sequence": sequence, "controls": list(values["transforms"]),
                     "rig_class": rig_class.get_name(), "match_base": True}
    return {"sequence": sequence, "check": report, "layer_job": layer_job,
            "window": (frames[first], frames[last]), "ease": (ease_in, ease_out)}


# ---------------------------------------------------------------------------
# Check: outside = the bake, inside = the rig, edges = no jump
# ---------------------------------------------------------------------------

def _jump(poses, bone, index):
    a, b = poses[bone][index].translation, poses[bone][index + 1].translation
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5


def check_window(sequence, binding, skeleton, mesh, baked, rig_name, clip, frames, window, covered):
    """Bake the sequence and compare it with the baked clip: outside the
    covered frames (should be the bake), inside the window (the rig's own
    error), and the extra frame-to-frame jump on the ease frames."""
    temp = f"{anim_paths.temp_folder(rig_name)}/A_{rig_name}_{clip}_Window_Check"
    played = anim_layers.export_to_anim_sequence(sequence, binding, skeleton, mesh, temp, template=baked)
    try:
        index = {f: i for i, f in enumerate(frames)}
        outside = [i for f, i in index.items() if f < covered[0] or f > covered[1]]
        inside = [i for f, i in index.items() if window[0] <= f <= window[1]]
        report = {
            "window": window, "covered": covered,
            "outside": anim_fk_layer.compare_with_bake(played, baked, outside) if outside else None,
            "inside": anim_fk_layer.compare_with_bake(played, baked, inside) if inside else None,
        }
        count = min(unreal.AnimationLibrary.get_num_keys(played), unreal.AnimationLibrary.get_num_keys(baked))
        pose0 = unreal.AnimPoseExtensions.get_anim_pose_at_frame(baked, 0, unreal.AnimPoseEvaluationOptions())
        bones = [str(b) for b in unreal.AnimPoseExtensions.get_bone_names(pose0)]
        a = anim_fk_layer._component_poses(played, bones, count)
        b = anim_fk_layer._component_poses(baked, bones, count)
        edge = [i for f, i in index.items() if covered[0] - 1 <= f <= covered[1] and i + 1 < count]
        worst = (0.0, None, None)
        for bone in bones:
            for i in edge:
                extra = _jump(a, bone, i) - _jump(b, bone, i)
                if extra > worst[0]:
                    worst = (extra, bone, frames[i])
        report["edge_extra_jump_cm"] = {"cm": round(worst[0], 4), "bone": worst[1], "frame": worst[2]}
    finally:
        unreal.EditorAssetLibrary.delete_directory(anim_paths.temp_folder(rig_name))
    out = report["outside"] or {}
    ins = report["inside"] or {}
    _log(f"Clip '{clip}' check: outside the window {out.get('position_cm')} cm from the bake; inside "
         f"{ins.get('position_cm')} cm (the rig's own error); largest extra jump on the ease frames "
         f"{report['edge_extra_jump_cm']['cm']} cm ({report['edge_extra_jump_cm']['bone']}, "
         f"frame {report['edge_extra_jump_cm']['frame']}).")
    return report
