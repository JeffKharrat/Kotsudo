"""Exact final export of animated clips -- run inside Unreal.

For each clip made by a retake method (docs/animation-export.md, section 17),
writes the final Anim Sequence: Maya's baked clip plus what the animator
layers change. Bones and morph curves the layers don't touch keep the bake's
own tracks, exactly Maya's.

* M1 full control bake (run_anim_layers.py): LS_<rig>_<clip> -> A_<rig>_<clip>_Final
* M2 FK layer (run_anim_fk_layer.py): LS_<rig>_<clip>_FK -> A_<rig>_<clip>_FK_Final

Then checks the result: against the bake (every method, no sidecar needed;
only the edited bones should differ) and, when the clip's sidecar is found,
against the Maya joints (report <base>.<clip>.final_check.json, M1). With an
empty animator layer the result is the bake itself.

Open the Control Rig (or set CONTROL_RIG_PATH). See
rig_builder/anim_layers.py (export_exact).
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

from rig_builder import anim_fk_layer, anim_import, anim_layers, manifest_schema  # noqa: E402
from rig_builder.metadata_reader import get_asset_metadata, read_manifest_document  # noqa: E402

CONTROL_RIG_PATH = None     # None = the open Control Rig
CLIP_NAMES = None           # None = every clip that has a layered sequence; else e.g. ["fk_body"]
METHODS = ("full_bake", "fk_layer")   # which retake methods' sequences to export
CHECK = True

# method: (sequence name suffix, final name suffix)
_METHODS = {"full_bake": ("", "_Final"), "fk_layer": (anim_fk_layer.FK_SUFFIX, "_FK_Final")}


def _mesh():
    if CONTROL_RIG_PATH:
        rig = unreal.EditorAssetLibrary.load_asset(CONTROL_RIG_PATH)
    else:
        rigs = unreal.ControlRigBlueprint.get_currently_open_rig_blueprints()
        rig = rigs[0] if rigs else None
    if rig is not None and rig.get_preview_mesh() is not None:
        return rig.get_preview_mesh()
    for asset in unreal.EditorUtilityLibrary.get_selected_assets() or []:
        if isinstance(asset, unreal.SkeletalMesh):
            return asset
    raise RuntimeError("Open the Control Rig (or set CONTROL_RIG_PATH, or select the Skeletal Mesh).")


def main():
    unreal.load_module("ControlRigDeveloper")
    unreal.load_module("ControlRigEditor")
    mesh = _mesh()
    skeleton = mesh.get_editor_property("skeleton")
    rig_name = mesh.get_name()
    metadata = {}
    metadata.update(get_asset_metadata(mesh))
    metadata.update(get_asset_metadata(skeleton))
    manifest = read_manifest_document(metadata)
    if manifest is not None:
        manifest, _notes = manifest_schema.migrate(manifest)
    sidecars = {}
    for clip_path in anim_import.find_all_clips([mesh, skeleton]):
        document = anim_import.load_clip(clip_path)
        if (document.get("rig_name") or rig_name) == rig_name:
            sidecars[document.get("clip")] = (clip_path, document)

    folder = anim_layers.SEQUENCE_FOLDER
    names = [p.split(".")[-1] for p in unreal.EditorAssetLibrary.list_assets(folder, False)]
    verdicts = []
    # Exports must not run while Sequencer shows another sequence: the bake
    # then plays the open one's state (frozen pose, 2026-11-24).
    unreal.LevelSequenceEditorBlueprintLibrary.close_level_sequence()
    for method in METHODS:
        sequence_suffix, final_suffix = _METHODS[method]
        prefix = f"LS_{rig_name}_"
        for name in names:
            if not name.startswith(prefix):
                continue
            clip = name[len(prefix):]
            if sequence_suffix:
                if not clip.endswith(sequence_suffix):
                    continue
                clip = clip[: -len(sequence_suffix)]
            elif any(clip.endswith(s) for _, (s, _f) in _METHODS.items() if s):
                continue
            if CLIP_NAMES and clip not in CLIP_NAMES:
                continue
            baked_path = f"{folder}/A_{rig_name}_{clip}"
            if not unreal.EditorAssetLibrary.does_asset_exist(baked_path):
                unreal.log_warning(f"[AnimExport] {name}: no baked clip {baked_path}, skipped.")
                continue
            sequence = unreal.EditorAssetLibrary.load_asset(f"{folder}/{name}")
            baked = unreal.EditorAssetLibrary.load_asset(baked_path)
            bindings = list(sequence.get_bindings())
            if not bindings:
                unreal.log_warning(f"[AnimExport] {name} has no binding, skipped.")
                continue
            final, summary = anim_layers.export_exact(sequence, bindings[0], baked, skeleton, mesh,
                                                      f"A_{rig_name}_{clip}{final_suffix}")
            if not CHECK:
                continue
            vs_bake = anim_fk_layer.compare_with_bake(final, baked)
            line = (f"VERDICT ({clip}, {method}): {len(summary['bones_changed'])} bone(s) edited; "
                    f"farthest from the bake {vs_bake['position_cm']} cm / {vs_bake['rotation_deg']} deg")
            unreal.log(f"[AnimExport] {line}")
            verdicts.append(line)
            if method == "full_bake" and clip in sidecars:
                clip_path, document = sidecars[clip]
                report_path = clip_path.replace(".anim.json", ".final_check.json")
                report = anim_import.check_anim_sequence(final, document, manifest, report_path,
                                                         label=f"{clip}, exact final export")
                report["edits"] = summary
                report["vs_bake"] = vs_bake
                with open(report_path, "w", encoding="utf-8") as handle:
                    json.dump(report, handle, indent=1)
                verdicts.append(report["verdict"])
    if not verdicts and CHECK:
        unreal.log_warning("[AnimExport] Nothing exported: build a retake sequence first "
                           "(Kotsudo > Animation > Retake method).")
    for verdict in verdicts:
        unreal.log(f"[AnimExport] {verdict}")


if __name__ == "__main__":
    main()
