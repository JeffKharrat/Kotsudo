"""Animation clips in Unreal: the shared start (S1) of both animation axes.

Reads a clip exported from Maya (tools/maya/export_animation.py):

* ``<base>.<clip>.anim.fbx`` is imported as an Anim Sequence on the rig's
  skeleton (the answer key, and the clip axis 1 retakes on);
* the Anim Sequence is then checked against the sidecar
  (``<base>.<clip>.anim.json``): every bone on every frame, compared with the
  Maya joint. This is the **measuring stick** of the animation check: it
  should be about 0, otherwise the comparison is wrong, not the rig.

Metric (same idea as the pose check): each bone's motion since the clip's
first frame, Unreal against Maya -- position change (cm) and rotation change
(deg). Constant per-bone axis differences from the importer cancel out.
Positions at the first frame are compared directly as a calibration.

Entry point: run_anim_import.py. Nothing here touches the Control Rig.
"""

import glob
import json
import math
import os

import unreal

from . import control_shapes

TIERS = (("T0", 0.01, 0.01), ("T1", 0.1, 0.5))
ANIM_FOLDER = "/Game/RigBuilder/Anims"
_INTERCHANGE_FBX_CVAR = "Interchange.FeatureFlags.Import.FBX"


def _log(message):
    unreal.log(f"[AnimImport] {message}")


def _warn(message):
    unreal.log_warning(f"[AnimImport] {message}")


# ---------------------------------------------------------------------------
# Finding the clip
# ---------------------------------------------------------------------------

def find_clip(assets, clip=None):
    """Path of a ``*.anim.json`` sidecar: the named clip, else the newest one,
    in the folders the rig FBX came from and the repo's FBXs folder."""
    for directory in control_shapes._search_directories(assets):
        if not directory or not os.path.isdir(directory):
            continue
        pattern = f"*.{clip}.anim.json" if clip else "*.anim.json"
        found = sorted(glob.glob(os.path.join(directory, pattern)), key=os.path.getmtime, reverse=True)
        if found:
            return found[0]
    return None


def find_all_clips(assets):
    """Every ``*.anim.json`` in the first search folder that has any."""
    for directory in control_shapes._search_directories(assets):
        if directory and os.path.isdir(directory):
            found = sorted(glob.glob(os.path.join(directory, "*.anim.json")))
            if found:
                return found
    return []


def load_clip(path):
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    if document.get("schema") != "kotsudo.anim":
        raise RuntimeError(f"{path}: not a Kotsudo animation clip.")
    return document


# ---------------------------------------------------------------------------
# Importing the baked FBX as an Anim Sequence
# ---------------------------------------------------------------------------

def _get_cvar(name):
    try:
        return unreal.SystemLibrary.get_console_variable_int_value(name)
    except Exception:
        return None


def _set_cvar(name, value):
    unreal.SystemLibrary.execute_console_command(None, f"{name} {int(value)}")


def import_baked_clip(fbx_path, skeleton, destination=ANIM_FOLDER, asset_name=None,
                      preserve_local_transform=False):
    """Import ``fbx_path`` as an Anim Sequence on ``skeleton``; returns it.

    Uses the classic FBX importer, whose animation-only options Unreal 5.6
    honours (its Interchange importer ignores the classic options, as found
    with the shape meshes); the Interchange switch is restored afterwards.

    ``preserve_local_transform``: take each bone's local transform from the
    FBX as is, instead of Unreal's default (local = global x parent global^-1,
    recomputed per frame). Under test: the default added a 0.025 cm leg
    offset on the clip where world_ctrl moves (docs/animation-export.md).
    """
    asset_name = asset_name or "A_" + os.path.basename(fbx_path).replace(".anim.fbx", "").replace(".", "_")
    remove_extra_imports(destination, asset_name, protect=skeleton)
    previous = _get_cvar(_INTERCHANGE_FBX_CVAR)
    if previous:
        _set_cvar(_INTERCHANGE_FBX_CVAR, 0)
    try:
        ui = unreal.FbxImportUI()
        # An automated import otherwise detects "skeletal mesh" from the file
        # and builds one (seen on the first run), whatever the other options.
        ui.set_editor_property("automated_import_should_detect_type", False)
        ui.set_editor_property("original_import_type", unreal.FBXImportType.FBXIT_ANIMATION)
        ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_ANIMATION)
        ui.set_editor_property("import_mesh", False)
        ui.set_editor_property("import_as_skeletal", True)
        ui.set_editor_property("import_animations", True)
        ui.set_editor_property("import_materials", False)
        ui.set_editor_property("import_textures", False)
        ui.set_editor_property("create_physics_asset", False)
        ui.set_editor_property("skeleton", skeleton)
        data = ui.get_editor_property("anim_sequence_import_data")
        data.set_editor_property("animation_length", unreal.FBXAnimationLengthImportType.FBXALIT_EXPORTED_TIME)
        data.set_editor_property("import_bone_tracks", True)
        for name, value in (("import_custom_attribute", True), ("remove_redundant_keys", False),
                            ("use_default_sample_rate", False),
                            ("preserve_local_transform", bool(preserve_local_transform))):
            try:
                data.set_editor_property(name, value)
            except Exception:
                pass

        task = unreal.AssetImportTask()
        task.set_editor_property("filename", fbx_path)
        task.set_editor_property("destination_path", destination)
        task.set_editor_property("destination_name", asset_name)
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("automated", True)
        task.set_editor_property("save", False)
        task.set_editor_property("options", ui)
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        paths = list(task.get_editor_property("imported_object_paths") or [])
    finally:
        if previous:
            _set_cvar(_INTERCHANGE_FBX_CVAR, previous)

    anim = None
    for path in paths:
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if isinstance(asset, unreal.AnimSequence) and anim is None:
            anim = asset
    if anim is None:
        # Some importer versions only report the package: look in the folder.
        for path in unreal.EditorAssetLibrary.list_assets(destination, recursive=False):
            asset = unreal.EditorAssetLibrary.load_asset(path)
            if isinstance(asset, unreal.AnimSequence) and asset.get_name().startswith(asset_name):
                anim = asset
                break
    if anim is None:
        raise RuntimeError(f"Importing {fbx_path} produced no Anim Sequence (imported: {paths}).")
    _log(f"Imported {os.path.basename(fbx_path)} -> {anim.get_path_name()}")
    remove_extra_imports(destination, asset_name, keep=anim, protect=skeleton)
    anim_skeleton = anim.get_editor_property("skeleton")
    if anim_skeleton != skeleton:
        _warn(f"The Anim Sequence uses skeleton {anim_skeleton.get_path_name() if anim_skeleton else None}, "
              f"not the rig's {skeleton.get_path_name()}.")
    return anim


def remove_extra_imports(destination, asset_name, keep=None, protect=None):
    """Delete what an import made besides the Anim Sequence (a skeletal mesh,
    physics asset or skeleton named after the clip, in our own folder).
    Run before an import too: a leftover mesh would block the name."""
    if not unreal.EditorAssetLibrary.does_directory_exist(destination):
        return
    used_skeleton = keep.get_editor_property("skeleton") if keep is not None else None
    for path in unreal.EditorAssetLibrary.list_assets(destination, recursive=False):
        asset = unreal.EditorAssetLibrary.load_asset(path)
        if asset is None or asset in (keep, protect, used_skeleton) or not asset.get_name().startswith(asset_name):
            continue
        if isinstance(asset, (unreal.SkeletalMesh, unreal.PhysicsAsset, unreal.Skeleton)):
            name = asset.get_path_name()
            if unreal.EditorAssetLibrary.delete_asset(path):
                _log(f"Removed {type(asset).__name__} {name} (made by the import, not needed).")


# ---------------------------------------------------------------------------
# The measuring stick: Anim Sequence bones vs the Maya joints
# ---------------------------------------------------------------------------

def _q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _q_inv(q):
    return (-q[0], -q[1], -q[2], q[3])


def _q_unit(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def _q_angle(a, b):
    """Angle between two rotations (normalised, atan2: no fake angle near 0)."""
    d = _q_mul(_q_inv(_q_unit(a)), _q_unit(b))
    return math.degrees(2.0 * math.atan2(math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2), abs(d[3])))


def _ue_state(transform):
    location = transform.translation
    rotation = transform.rotation
    return ((location.x, location.y, location.z), (rotation.x, rotation.y, rotation.z, rotation.w))


def _num_frames(anim):
    try:
        return int(unreal.AnimationLibrary.get_num_frames(anim))
    except Exception:
        return None


def _tier(position, rotation):
    for name, p_max, r_max in TIERS:
        if position <= p_max and rotation <= r_max:
            return name
    return "above T1"


def check_anim_sequence(anim, document, manifest=None, report_path=None, label="baked clip"):
    """Compare every bone of ``anim`` on every frame with the sidecar's Maya
    joints. Returns the report dict (and writes it to ``report_path``)."""
    frames = document["time"]["frames"]
    maya_joints = document.get("joints") or {}
    module_of = {}
    for module in (manifest or {}).get("modules") or []:
        for bone in module.get("chain") or []:
            module_of[str(bone).split("|")[-1]] = module.get("module_name")

    options = unreal.AnimPoseEvaluationOptions()
    poses = [unreal.AnimPoseExtensions.get_anim_pose_at_frame(anim, index, options)
             for index in range(len(frames))]
    bones = {str(name) for name in unreal.AnimPoseExtensions.get_bone_names(poses[0])}
    # Like the pose check, only joints a module rebuilds are scored: helper
    # chains (separate IK/FK joints behind a blended limb) are listed apart.
    not_rebuilt = sorted(n for n in maya_joints if module_of and n not in module_of)
    compared = sorted(name for name in maya_joints if name in bones and name not in not_rebuilt)
    missing = sorted(name for name in maya_joints if name not in bones)
    world = unreal.AnimPoseSpaces.WORLD

    per_joint = {}
    per_frame = [0.0] * len(frames)
    calibration = []
    for name in compared:
        maya = maya_joints[name]
        ue = [_ue_state(unreal.AnimPoseExtensions.get_bone_pose(pose, name, world)) for pose in poses]
        (ue_p0, ue_q0), (m_p0, m_q0) = ue[0], maya[0]
        calibration.append((name, math.dist(ue_p0, m_p0)))
        worst_p = worst_r = 0.0
        worst_frame, worst_vectors = frames[0], None
        for index in range(len(frames)):
            ue_p, ue_q = ue[index]
            m_p, m_q = maya[index]
            ue_d = [ue_p[i] - ue_p0[i] for i in range(3)]
            maya_d = [m_p[i] - m_p0[i] for i in range(3)]
            e_p = math.dist(ue_d, maya_d)
            e_r = _q_angle(_q_mul(ue_q, _q_inv(ue_q0)), _q_mul(tuple(m_q), _q_inv(tuple(m_q0))))
            if e_p > worst_p:
                worst_frame, worst_vectors = frames[index], (ue_d, maya_d)
            worst_p, worst_r = max(worst_p, e_p), max(worst_r, e_r)
            per_frame[index] = max(per_frame[index], e_p)
        per_joint[name] = {"position_cm": round(worst_p, 5), "rotation_deg": round(worst_r, 5),
                           "worst_frame": worst_frame, "module": module_of.get(name)}
        if worst_vectors:
            # Diagnosis: is the error a scale (same direction, other length)
            # or an offset (other direction)?
            ue_d, maya_d = worst_vectors
            ue_len, maya_len = math.sqrt(sum(c * c for c in ue_d)), math.sqrt(sum(c * c for c in maya_d))
            cos = sum(a * b for a, b in zip(ue_d, maya_d)) / (ue_len * maya_len) if ue_len and maya_len else 1.0
            per_joint[name]["at_worst_frame"] = {
                "unreal_motion_cm": [round(c, 4) for c in ue_d], "maya_motion_cm": [round(c, 4) for c in maya_d],
                "length_ratio": round(ue_len / maya_len, 6) if maya_len else None,
                "direction_deg": round(math.degrees(math.acos(max(-1.0, min(1.0, cos)))), 5)}

    by_module = {}
    for name, data in per_joint.items():
        module = data["module"] or "(no module)"
        entry = by_module.setdefault(module, {"position_cm": 0.0, "rotation_deg": 0.0, "worst_joint": None})
        if data["position_cm"] >= entry["position_cm"]:
            entry["worst_joint"] = name
        entry["position_cm"] = max(entry["position_cm"], data["position_cm"])
        entry["rotation_deg"] = max(entry["rotation_deg"], data["rotation_deg"])
    for entry in by_module.values():
        entry["tier"] = _tier(entry["position_cm"], entry["rotation_deg"])

    worst_p = max((d["position_cm"] for d in per_joint.values()), default=0.0)
    worst_r = max((d["rotation_deg"] for d in per_joint.values()), default=0.0)
    calibration.sort(key=lambda item: -item[1])
    report = {
        "schema": "kotsudo.anim_check", "label": label,
        "clip": document.get("clip"), "anim_sequence": anim.get_path_name(),
        "frames": {"sidecar": len(frames), "anim_sequence": _num_frames(anim)},
        "joints": {"compared": len(compared), "missing_in_unreal": missing, "not_rebuilt": not_rebuilt},
        "diagnosis": {n: per_joint[n].get("at_worst_frame") for n, _ in
                      sorted(per_joint.items(), key=lambda item: -item[1]["position_cm"])[:8]},
        "worst": {"position_cm": round(worst_p, 5), "rotation_deg": round(worst_r, 5),
                  "tier": _tier(worst_p, worst_r)},
        "calibration_first_frame_cm": [(n, round(e, 4)) for n, e in calibration[:10]],
        "by_module": dict(sorted(by_module.items())),
        "per_frame_max_position_cm": [round(v, 5) for v in per_frame],
        "worst_joints": sorted(((n, d["position_cm"], d["rotation_deg"], d["worst_frame"])
                                for n, d in per_joint.items()), key=lambda item: (-item[1], -item[2]))[:10],
    }
    verdict = (f"VERDICT ({label}): {report['worst']['tier']} -- worst {worst_p:.4f} cm / {worst_r:.4f} deg "
               f"over {len(compared)} bone(s) x {len(frames)} frame(s)")
    report["verdict"] = verdict
    _log(verdict)
    if report["frames"]["anim_sequence"] not in (None, len(frames), len(frames) - 1):
        _warn(f"Frame count differs: sidecar {len(frames)}, Anim Sequence {report['frames']['anim_sequence']}.")
    if missing:
        _warn(f"{len(missing)} Maya joint(s) not in the Anim Sequence: {missing[:6]}")
    if calibration:
        (_warn if calibration[0][1] > 0.1 else _log)(
            f"  first frame, bones farthest from their Maya position (cm): {report['calibration_first_frame_cm'][:3]}")
    for name, p, r, frame in report["worst_joints"][:5]:
        diag = per_joint[name].get("at_worst_frame") or {}
        _log(f"  {name}: {p} cm / {r} deg (frame {frame}; motion length ratio "
             f"{diag.get('length_ratio')}, direction off by {diag.get('direction_deg')} deg)")
    if not_rebuilt:
        _log(f"  {len(not_rebuilt)} helper joint(s) not rebuilt by any module are not scored "
             f"(e.g. {', '.join(not_rebuilt[:4])}).")
    if report_path:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1)
        _log(f"Report: {report_path}")
    return report
