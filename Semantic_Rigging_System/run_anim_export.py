"""Exact final export of animated clips -- run inside Unreal.

For each clip made by run_anim_layers.py (Level Sequence LS_<rig>_<clip>,
baked clip A_<rig>_<clip>), writes /Game/RigBuilder/Anims/A_<rig>_<clip>_Final:
Maya's baked clip plus what the animator layers change. Bones and morph
curves the layers don't touch keep the bake's own tracks, exactly Maya's.

Then checks the result against the Maya joints in the clip's sidecar
(report <base>.<clip>.final_check.json). With an empty animator layer the
result is the bake itself (the measuring stick, T0).

Open the Control Rig (or set CONTROL_RIG_PATH). See
rig_builder/anim_layers.py (export_exact) and docs/animation-export.md.
"""

import importlib
import json
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
CLIP_NAMES = None           # None = every clip that has a layered sequence; else e.g. ["fk_body"]
CHECK = True


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
    metadata = {}
    metadata.update(get_asset_metadata(mesh))
    metadata.update(get_asset_metadata(skeleton))
    manifest = read_manifest_document(metadata)
    if manifest is not None:
        manifest, _notes = manifest_schema.migrate(manifest)

    folder = anim_layers.SEQUENCE_FOLDER
    verdicts = []
    for clip_path in anim_import.find_all_clips([mesh, skeleton]):
        document = anim_import.load_clip(clip_path)
        clip, rig_name = document.get("clip"), document.get("rig_name") or "rig"
        if CLIP_NAMES and clip not in CLIP_NAMES:
            continue
        sequence_path = f"{folder}/LS_{rig_name}_{clip}"
        baked_path = f"{folder}/A_{rig_name}_{clip}"
        if not (unreal.EditorAssetLibrary.does_asset_exist(sequence_path)
                and unreal.EditorAssetLibrary.does_asset_exist(baked_path)):
            continue  # not made by run_anim_layers.py (or another rig's clip)
        sequence = unreal.EditorAssetLibrary.load_asset(sequence_path)
        baked = unreal.EditorAssetLibrary.load_asset(baked_path)
        bindings = list(sequence.get_bindings())
        if not bindings:
            unreal.log_warning(f"[AnimExport] {sequence_path} has no binding, skipped.")
            continue
        final, summary = anim_layers.export_exact(sequence, bindings[0], baked, skeleton, mesh,
                                                  f"A_{rig_name}_{clip}_Final")
        if CHECK:
            report_path = clip_path.replace(".anim.json", ".final_check.json")
            report = anim_import.check_anim_sequence(final, document, manifest, report_path,
                                                     label=f"{clip}, exact final export")
            report["edits"] = summary
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=1)
            verdicts.append(report["verdict"])
    for verdict in verdicts:
        unreal.log(f"[AnimExport] {verdict}")


if __name__ == "__main__":
    main()
