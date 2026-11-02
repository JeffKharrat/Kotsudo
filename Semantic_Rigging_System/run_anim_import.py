"""Animation clip import, shared start (S1) -- run inside Unreal.

Imports a clip exported from Maya (Animation tab of the tagger tool) as an
Anim Sequence on the open rig's skeleton, then checks it bone by bone, frame
by frame, against the Maya joints in the clip's sidecar. The verdict should
be T0: that is the measuring stick every later animation step is judged by.

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
CLIP_NAME = None            # None = the newest *.anim.json next to the rig FBX; else e.g. "fk_body"
CLIP_PATH = None            # or a full path to a <base>.<clip>.anim.json


def main():
    unreal.load_module("ControlRigDeveloper")
    if CONTROL_RIG_PATH:
        rig = unreal.EditorAssetLibrary.load_asset(CONTROL_RIG_PATH)
    else:
        rigs = unreal.ControlRigBlueprint.get_currently_open_rig_blueprints()
        if not rigs:
            raise RuntimeError("No Control Rig Blueprint is open. Open one or set CONTROL_RIG_PATH.")
        rig = rigs[0]

    builder = RigBuilder(source_asset_path=SOURCE_ASSET_PATH, rig=rig)
    source = builder.load_source_asset()
    skeleton = builder.resolve_skeleton(source)
    mesh = source if isinstance(source, unreal.SkeletalMesh) else None
    metadata = {}
    metadata.update(get_asset_metadata(mesh))
    metadata.update(get_asset_metadata(skeleton))
    manifest = read_manifest_document(metadata)
    if manifest is not None:
        manifest, _notes = manifest_schema.migrate(manifest)

    assets = [mesh, skeleton, rig.get_preview_mesh() if hasattr(rig, "get_preview_mesh") else None]
    clip_path = CLIP_PATH or anim_import.find_clip(assets, CLIP_NAME)
    if not clip_path:
        raise RuntimeError("No <base>.<clip>.anim.json found next to the rig FBX or in the repo's FBXs "
                           "folder. Export a clip from Maya (Animation tab), or set CLIP_PATH.")
    document = anim_import.load_clip(clip_path)
    folder = os.path.dirname(clip_path)
    fbx_name = document.get("fbx_file")
    if not fbx_name or not os.path.isfile(os.path.join(folder, fbx_name)):
        raise RuntimeError(f"{clip_path}: no baked FBX beside it. Re-export the clip with 'Baked FBX' ticked.")

    anim = anim_import.import_baked_clip(os.path.join(folder, fbx_name), skeleton)
    report_path = clip_path.replace(".anim.json", ".anim_check.json")
    anim_import.check_anim_sequence(anim, document, manifest, report_path)


if __name__ == "__main__":
    main()
