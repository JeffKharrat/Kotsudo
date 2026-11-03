"""Export a rig and its script-made test clips for Unreal (run with mayapy on a scene copy).

    "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya_export_rig_and_clips.py <scene_copy.ma> <out_dir> <base_name> [clip ...]

Writes, in <out_dir>: <base_name>.fbx (skeleton, meshes, manifest on the root
joint), its control shapes and test poses, and for each clip (default: all of
tools/maya/test_animations.CLIPS) <base_name>.<clip>.anim.json with its baked
<base_name>.<clip>.anim.fbx. Works on tagged rigs (V06) and Jeffy rigs (V010).
Then, in Unreal: import <base_name>.fbx, open a Control Rig on it, and run
run_anim_import.py (it picks up every clip beside the FBX).

The scene is never saved.
"""
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "maya"))
import export_animation as ea  # noqa: E402
import export_rig_manifest as erm  # noqa: E402
import rig_tagger_tool  # noqa: E402
import test_animations as ta  # noqa: E402


def main():
    scene, out_dir, base = sys.argv[1], os.path.abspath(sys.argv[2]), sys.argv[3]
    clips = sys.argv[4:] or list(ta.CLIPS)
    cmds.file(scene, open=True, force=True, ignoreVersion=True)
    cmds.loadPlugin("fbxmaya", quiet=True)

    modules = rig_tagger_tool.scene_modules()
    fbx = erm.export(out_dir, base, base, modules)
    print("rig:", fbx, erm.LAST_EXPORT_SUMMARY)

    manifest = ea.manifest_from_scene()
    for clip in clips:
        start, end = ta.make_clip(clip)
        summary = ea.export_clip(manifest, out_dir, clip, start, end, bake_fbx=True)
        print("clip {}: {} frames, {} keyed channels -> {}".format(
            clip, summary["frames"], summary["keyed_channels"], os.path.basename(summary.get("fbx") or "")))
    ta.clear_animation()
    return 0


if __name__ == "__main__":
    code = main()
    os._exit(code)
