"""Maya clip -> Control Rig base layer + animator layer -- run inside Unreal.

For each clip exported from Maya (Animation tab of the tagger tool), makes
/Game/RigBuilder/Anims/LS_<rig>_<clip>: the rig's mesh with a Control Rig
track whose base layer holds the clip on the controls (locked) and an empty
additive "Animator" layer on top. Then bakes the sequence to an Anim
Sequence with Unreal's own evaluation and checks every bone on every frame
against Maya's joints (report <base>.<clip>.layers_check.json).

Open the Control Rig (or set CONTROL_RIG_PATH). See rig_builder/anim_layers.py
and docs/animation-export.md.
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

from rig_builder import anim_import, anim_layers, manifest_schema  # noqa: E402
from rig_builder.builder import RigBuilder  # noqa: E402
from rig_builder.metadata_reader import get_asset_metadata, read_manifest_document  # noqa: E402

CONTROL_RIG_PATH = None     # None = the open Control Rig
CLIP_NAMES = None           # None = every clip of this rig; else e.g. ["fk_body", "spine"]
CLIP_PATH = None            # or a full path to one <base>.<clip>.anim.json
ADD_ANIMATOR_LAYER = True
CHECK = True                # bake the sequence and compare with Maya's joints
REIMPORT_BAKED = False      # True = import the clip's baked FBX again even if its Anim Sequence exists


def _rig_class(rig_blueprint):
    for getter in ("generated_class", "get_generated_class"):
        method = getattr(rig_blueprint, getter, None)
        if method is not None:
            try:
                cls = method()
                if cls is not None:
                    return cls
            except Exception:
                pass
    path = rig_blueprint.get_path_name()
    return unreal.load_class(None, f"{path}_C")


def main():
    unreal.load_module("ControlRigDeveloper")
    unreal.load_module("ControlRigEditor")
    if CONTROL_RIG_PATH:
        rig = unreal.EditorAssetLibrary.load_asset(CONTROL_RIG_PATH)
    else:
        rigs = unreal.ControlRigBlueprint.get_currently_open_rig_blueprints()
        rig = rigs[0] if rigs else None
    if rig is None:
        raise RuntimeError("Open the Control Rig (or set CONTROL_RIG_PATH).")

    builder = RigBuilder(source_asset_path=None, rig=rig)
    source = builder.load_source_asset()
    skeleton = builder.resolve_skeleton(source)
    mesh = source if isinstance(source, unreal.SkeletalMesh) else rig.get_preview_mesh()
    if mesh is None:
        raise RuntimeError("The rig has no preview Skeletal Mesh to put in the sequence.")
    metadata = {}
    metadata.update(get_asset_metadata(mesh))
    metadata.update(get_asset_metadata(skeleton))
    manifest = read_manifest_document(metadata)
    if manifest is not None:
        manifest, _notes = manifest_schema.migrate(manifest)

    if CLIP_PATH:
        clip_paths = [CLIP_PATH]
    else:
        clip_paths = anim_import.find_all_clips([mesh, skeleton])
    skeleton_bones = set(builder.get_bone_names(skeleton))
    results = []
    for clip_path in clip_paths:
        document = anim_import.load_clip(clip_path)
        if CLIP_NAMES and document.get("clip") not in CLIP_NAMES:
            continue
        joints = set(document.get("joints") or {})
        if len(joints - skeleton_bones) * 2 > len(joints):
            unreal.log_warning(f"[AnimLayers] {os.path.basename(clip_path)}: made for another skeleton, skipped.")
            continue
        # The baked clip (route A) next to it: the layer the final export
        # starts from, and the check's frame-rate template.
        baked = None
        fbx_name = document.get("fbx_file")
        fbx_path = os.path.join(os.path.dirname(clip_path), fbx_name) if fbx_name else None
        if fbx_path and os.path.isfile(fbx_path):
            asset_name = "A_" + fbx_name.replace(".anim.fbx", "").replace(".", "_")
            asset_path = f"{anim_layers.SEQUENCE_FOLDER}/{asset_name}"
            if REIMPORT_BAKED or not unreal.EditorAssetLibrary.does_asset_exist(asset_path):
                baked = anim_import.import_baked_clip(fbx_path, skeleton, asset_name=asset_name)
            else:
                baked = unreal.EditorAssetLibrary.load_asset(asset_path)
        else:
            unreal.log_warning(f"[AnimLayers] {os.path.basename(clip_path)}: no baked FBX beside it.")
        report_path = clip_path.replace(".anim.json", ".layers_check.json")
        result = anim_layers.build_layered_clip(rig, _rig_class(rig), mesh, skeleton, document, manifest,
                                                report_path, add_layer=ADD_ANIMATOR_LAYER, check=CHECK,
                                                baked_anim=baked)
        if result["report"]:
            results.append(result["report"]["verdict"])
    for verdict in results:
        unreal.log(f"[AnimLayers] {verdict}")


if __name__ == "__main__":
    main()
