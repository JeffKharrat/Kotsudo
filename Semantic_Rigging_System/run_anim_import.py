"""Animation clip import, shared start (S1) -- run inside Unreal.

Imports a clip exported from Maya (Animation tab of the tagger tool) as an
Anim Sequence on the open rig's skeleton, then checks it bone by bone, frame
by frame, against the Maya joints in the clip's sidecar. The verdict should
be T0: that is the measuring stick every later animation step is judged by.

The skeleton is the open Control Rig's (or SOURCE_ASSET_PATH, or the
Skeletal Mesh selected in the Content Browser when no rig is open). Clips
made for another skeleton (another rig's clips in the same folder) are
skipped.

Writes <base>.<clip>.anim_check.json next to the sidecar. See
rig_builder/anim_import.py and docs/animation-export.md.
"""

import importlib
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
for name in [m for m in list(sys.modules) if m == "rig_builder" or m.startswith("rig_builder.")]:
    del sys.modules[name]
importlib.invalidate_caches()

import unreal  # noqa: E402

from rig_builder import anim_import, manifest_schema  # noqa: E402
from rig_builder.builder import RigBuilder  # noqa: E402
from rig_builder.metadata_reader import get_asset_metadata, read_manifest_document  # noqa: E402

CONTROL_RIG_PATH = None     # None = the open Control Rig
SOURCE_ASSET_PATH = None    # None = derived from the rig's preview mesh, as in run_rig_builder
ALL_CLIPS = True            # True = every *.anim.json next to the rig FBX (or in the repo's FBXs)
CLIP_NAME = None            # with ALL_CLIPS = False: None = the newest clip; else e.g. "fk_body"
CLIP_PATH = None            # or a full path to a <base>.<clip>.anim.json
# Import mode of the bone tracks: False = Unreal's default (local recomputed
# from global transforms), True = the FBX local transforms as they are,
# None = import both, report both verdicts. Tested 2026-11-02 on two clips:
# identical results, so the default is kept.
PRESERVE_LOCAL_TRANSFORM = False


def _selected_skeletal_asset():
    """The Skeletal Mesh (or Skeleton) selected in the Content Browser, or None."""
    for asset in unreal.EditorUtilityLibrary.get_selected_assets() or []:
        if isinstance(asset, (unreal.SkeletalMesh, unreal.Skeleton)):
            return asset
    return None


def main():
    unreal.load_module("ControlRigDeveloper")
    # The skeleton comes from, in order: SOURCE_ASSET_PATH, the open Control
    # Rig's preview mesh, or a Skeletal Mesh selected in the Content Browser
    # (no Control Rig needed: the baked clip check only uses the skeleton).
    rig = None
    if CONTROL_RIG_PATH:
        rig = unreal.EditorAssetLibrary.load_asset(CONTROL_RIG_PATH)
    elif not SOURCE_ASSET_PATH:
        rigs = unreal.ControlRigBlueprint.get_currently_open_rig_blueprints()
        rig = rigs[0] if rigs else None

    builder = RigBuilder(source_asset_path=SOURCE_ASSET_PATH, rig=rig)
    if SOURCE_ASSET_PATH or rig:
        source = builder.load_source_asset()
    else:
        source = _selected_skeletal_asset()
        if not source:
            raise RuntimeError("No skeleton: open a Control Rig, select the rig's Skeletal Mesh in the "
                               "Content Browser, or set SOURCE_ASSET_PATH.")
    skeleton = builder.resolve_skeleton(source)
    unreal.log(f"[AnimImport] Skeleton: {skeleton.get_path_name()}")
    mesh = source if isinstance(source, unreal.SkeletalMesh) else None
    metadata = {}
    metadata.update(get_asset_metadata(mesh))
    metadata.update(get_asset_metadata(skeleton))
    manifest = read_manifest_document(metadata)
    if manifest is not None:
        manifest, _notes = manifest_schema.migrate(manifest)

    assets = [mesh, skeleton, rig.get_preview_mesh() if rig and hasattr(rig, "get_preview_mesh") else None]
    if CLIP_PATH:
        clip_paths = [CLIP_PATH]
    elif ALL_CLIPS:
        clip_paths = anim_import.find_all_clips(assets)
    else:
        clip_paths = [p for p in [anim_import.find_clip(assets, CLIP_NAME)] if p]
    if not clip_paths:
        raise RuntimeError("No <base>.<clip>.anim.json found next to the rig FBX or in the repo's FBXs "
                           "folder. Export a clip from Maya (Animation tab), or set CLIP_PATH.")

    skeleton_bones = set(builder.get_bone_names(skeleton))
    verdicts = []
    for clip_path in clip_paths:
        document = anim_import.load_clip(clip_path)
        folder = os.path.dirname(clip_path)
        clip_joints = set(document.get("joints") or {})
        missing = sorted(clip_joints - skeleton_bones)
        if len(missing) * 2 > len(clip_joints):
            # Another rig's clip in the same folder (e.g. V06 clips beside a V010 skeleton).
            unreal.log_warning(f"[AnimImport] {os.path.basename(clip_path)}: made for another skeleton "
                               f"({len(missing)} of {len(clip_joints)} joints not in {skeleton.get_name()}), "
                               "skipped.")
            continue
        if missing:
            unreal.log_warning(f"[AnimImport] {os.path.basename(clip_path)}: {len(missing)} joint(s) not in "
                               f"the skeleton: {', '.join(missing[:8])}")
        fbx_name = document.get("fbx_file")
        if not fbx_name or not os.path.isfile(os.path.join(folder, fbx_name)):
            unreal.log_warning(f"[AnimImport] {os.path.basename(clip_path)}: no baked FBX beside it, skipped "
                               "(re-export the clip with 'Baked FBX' ticked).")
            continue
        modes = (False, True) if PRESERVE_LOCAL_TRANSFORM is None else (bool(PRESERVE_LOCAL_TRANSFORM),)
        for preserve in modes:
            suffix = "_local" if preserve else ""
            base = "A_" + fbx_name.replace(".anim.fbx", "").replace(".", "_")
            anim = anim_import.import_baked_clip(os.path.join(folder, fbx_name), skeleton,
                                                 asset_name=base + suffix, preserve_local_transform=preserve)
            report_path = clip_path.replace(".anim.json", f".anim_check{suffix}.json")
            mode = "FBX local transforms" if preserve else "Unreal default import"
            report = anim_import.check_anim_sequence(anim, document, manifest, report_path,
                                                     label=f"baked {document.get('clip')}, {mode}")
            verdicts.append(report["verdict"])
    for verdict in verdicts:
        unreal.log(f"[AnimImport] {verdict}")


if __name__ == "__main__":
    main()
