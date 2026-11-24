"""Retake method M3: the retake window -- run inside Unreal.

The baked clip plays everywhere; the Kotsudo rig takes over only on the
frames you want to retake (WINDOW), easing in and out over EASE_FRAMES
(docs/animation-export.md, section 17). Makes
/Game/RigBuilder/Anims/<rig>/Retakes/LS_<rig>_<clip>_Window with an Animator
layer on the Kotsudo rig, over the window. Open it in Sequencer and key the
Animator layer.

Needs: the Kotsudo Control Rig open (or CONTROL_RIG_PATH), the clip's baked
FBX and its .anim.json (Maya's control spots for the window frames).
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

from rig_builder import anim_import, anim_layers, anim_paths, anim_window  # noqa: E402
from rig_builder.builder import RigBuilder  # noqa: E402

CONTROL_RIG_PATH = None     # None = the open Control Rig
CLIP_NAMES = None           # None = every clip of this rig; else e.g. ["fk_body"]
WINDOW = None               # (first, last) frame to retake, Maya frame numbers; None = the middle third
EASE_FRAMES = 3             # frames to blend from the bake into the rig, and back
ADD_ANIMATOR_LAYER = True
CHECK = True                # outside = the bake? inside = the rig? jumps at the edges?


def _rig_class(rig_blueprint):
    path = rig_blueprint.get_path_name().split(".")[0]
    return unreal.load_class(None, f"{path}.{path.split('/')[-1]}_C")


def main():
    unreal.load_module("ControlRigDeveloper")
    unreal.load_module("ControlRigEditor")
    if CONTROL_RIG_PATH:
        rig = unreal.EditorAssetLibrary.load_asset(CONTROL_RIG_PATH)
    else:
        rigs = unreal.ControlRigBlueprint.get_currently_open_rig_blueprints()
        rig = rigs[0] if rigs else None
    if rig is None:
        raise RuntimeError("Open the Kotsudo Control Rig (or set CONTROL_RIG_PATH).")
    builder = RigBuilder(source_asset_path=None, rig=rig)
    source = builder.load_source_asset()
    skeleton = builder.resolve_skeleton(source)
    mesh = source if isinstance(source, unreal.SkeletalMesh) else rig.get_preview_mesh()

    clips = []
    for clip_path in anim_import.find_all_clips([mesh, skeleton]):
        document = anim_import.load_clip(clip_path)
        if CLIP_NAMES and document.get("clip") not in CLIP_NAMES:
            continue
        rig_name = document.get("rig_name") or mesh.get_name()
        if rig_name != mesh.get_name():
            continue
        baked_path = anim_paths.baked(rig_name, document.get("clip"))
        if not unreal.EditorAssetLibrary.does_asset_exist(baked_path):
            unreal.log_warning(f"[RetakeWindow] {document.get('clip')}: not imported yet "
                               "(Kotsudo > Animation > 1. Import baked clips), skipped.")
            continue
        clips.append((document, unreal.EditorAssetLibrary.load_asset(baked_path)))
    if not clips:
        raise RuntimeError("No clip to retake: import the clips first (Kotsudo > Animation > 1).")
    anim_layers.close_sequencer_if_showing(
        {anim_paths.sequence(d.get("rig_name") or mesh.get_name(), d.get("clip"), "window") for d, _ in clips})

    layer_jobs = []
    for document, baked in clips:
        frames = document["time"]["frames"]
        window = WINDOW
        if window is None:
            third = max(1, len(frames) // 3)
            window = (int(round(frames[third])), int(round(frames[min(len(frames) - 1, 2 * third)])))
        result = anim_window.build_window_clip(rig, _rig_class(rig), mesh, skeleton, document, baked, window,
                                               ease=EASE_FRAMES, add_layer=ADD_ANIMATOR_LAYER, check=CHECK)
        if result["layer_job"]:
            layer_jobs.append(result["layer_job"])
    if layer_jobs:
        unreal.log(f"[RetakeWindow] Adding the animator layer to {len(layer_jobs)} sequence(s) over the next "
                   "frames; wait for the 'Animator layer added' line before animating.")
        anim_layers.add_layers_later(layer_jobs)


if __name__ == "__main__":
    main()
