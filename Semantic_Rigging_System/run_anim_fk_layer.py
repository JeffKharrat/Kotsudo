"""Retake method M2: baked clip -> FK Control Rig base layer + animator layer -- run inside Unreal.

The lightest retake method (docs/animation-export.md, section 17). Needs only
the baked clip, imported as an Anim Sequence (Kotsudo > Animation > Import
baked clips, or run_anim_import.py): no sidecar, no Kotsudo rig.

For each clip makes:

* /Game/RigBuilder/Anims/<rig>/Rigs/CR_<rig>_FK (once per rig): one FK control per bone;
* /Game/RigBuilder/Anims/<rig>/Retakes/LS_<rig>_<clip>_FK: the clip as keys on those
  controls (locked base layer) and an empty additive "Animator" layer.

Then checks the base layer against the bake (Unreal's own playback, every
bone on every frame). The final Anim Sequence comes from the Final export
step (run_anim_export.py), as for the other methods.

Which clips: CLIP_NAMES, else the Anim Sequences selected in the Content
Browser, else every baked clip in /Game/RigBuilder/Anims/<rig>/Clips.
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

from rig_builder import anim_fk_layer, anim_layers, anim_paths  # noqa: E402

CONTROL_RIG_PATH = None     # None = the open Control Rig (only its preview mesh is used)
MESH_PATH = None            # or the Skeletal Mesh directly (then no Control Rig is needed)
CLIP_NAMES = None           # e.g. ["fk_body"]; None = the selected Anim Sequences, else every baked clip
ADD_ANIMATOR_LAYER = True
CHECK = True                # compare the base layer's playback with the bake
REBUILD_FK_RIG = False      # True = rebuild CR_<rig>_FK's controls and graph

def _mesh():
    if MESH_PATH:
        return unreal.EditorAssetLibrary.load_asset(MESH_PATH)
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
    raise RuntimeError("No Skeletal Mesh: open the rig's Control Rig, select its Skeletal Mesh in the "
                       "Content Browser, or set MESH_PATH.")


def _clips(rig_name, skeleton):
    """[(clip name, baked Anim Sequence)]."""
    prefix = f"A_{rig_name}_"
    selected = [] if CLIP_NAMES else [a for a in unreal.EditorUtilityLibrary.get_selected_assets() or []
                                      if isinstance(a, unreal.AnimSequence)]
    if selected:
        candidates = selected
    else:
        candidates = [unreal.EditorAssetLibrary.load_asset(p) for p in anim_paths.baked_clips(rig_name).values()]
        candidates = [a for a in candidates if isinstance(a, unreal.AnimSequence)]
    clips = []
    for anim in candidates:
        name = anim.get_name()
        clip = name[len(prefix):] if name.startswith(prefix) else name
        if CLIP_NAMES and clip not in CLIP_NAMES:
            continue
        if anim.get_editor_property("skeleton") != skeleton:
            unreal.log_warning(f"[FKLayer] {name}: another skeleton, skipped.")
            continue
        clips.append((clip, anim))
    return clips


def main():
    unreal.load_module("ControlRigDeveloper")
    unreal.load_module("ControlRigEditor")
    mesh = _mesh()
    skeleton = mesh.get_editor_property("skeleton")
    rig_name = mesh.get_name()
    clips = _clips(rig_name, skeleton)
    if not clips:
        raise RuntimeError(f"No baked clip of {rig_name} in {anim_paths.folder(rig_name, 'Clips')}: import the clips "
                           "first (Kotsudo > Animation > Import baked clips).")
    anim_layers.close_sequencer_if_showing(
        {anim_paths.sequence(rig_name, c, "fk_layer") for c, _ in clips})
    layer_jobs = []
    rebuild = REBUILD_FK_RIG
    for clip, baked in clips:
        result = anim_fk_layer.build_fk_clip(baked, mesh, skeleton, rig_name, clip,
                                             add_layer=ADD_ANIMATOR_LAYER, check=CHECK, rebuild_rig=rebuild)
        rebuild = False
        if result["layer_job"]:
            layer_jobs.append(result["layer_job"])
    if layer_jobs:
        unreal.log(f"[FKLayer] Adding the animator layer to {len(layer_jobs)} sequence(s) over the next "
                   "frames; wait for the 'Animator layer added' line before animating.")
        anim_layers.add_layers_later(layer_jobs)


if __name__ == "__main__":
    main()
