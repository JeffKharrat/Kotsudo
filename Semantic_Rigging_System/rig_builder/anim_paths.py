"""Where the animation steps put their assets: one folder per rig, one
sub-folder per kind, so it is clear what each asset is for.

    /Game/RigBuilder/Anims/<rig>/
        Clips/     A_<rig>_<clip>                  the baked clips imported from Maya
                                                   (step 1; every retake method starts from them)
        Retakes/   LS_<rig>_<clip>_FullBake        M1: the Kotsudo rig's controls hold the whole clip
                   LS_<rig>_<clip>_FK              M2: one FK control per bone holds the clip
                   LS_<rig>_<clip>_Window          M3: the bake, the Kotsudo rig only on the window
                   (open these in Sequencer and key the Animator layer)
        Rigs/      CR_<rig>_FK                     the generated FK rig (M2, M3)
        Final/     A_<rig>_<clip>_<method>_Final   optional: a retake turned into an Anim Sequence
                                                   (step 3)

The checks' temporary bakes go to ``Temp`` and are deleted right after.
"""

import unreal

ROOT = "/Game/RigBuilder/Anims"

# method id: (name suffix, label)
METHODS = {
    "full_bake": ("FullBake", "M1 full control bake"),
    "fk_layer": ("FK", "M2 FK layer"),
    "window": ("Window", "M3 retake window"),
}


def folder(rig, kind):
    return f"{ROOT}/{rig}/{kind}"


def baked(rig, clip):
    return f"{folder(rig, 'Clips')}/A_{rig}_{clip}"


def sequence(rig, clip, method):
    return f"{folder(rig, 'Retakes')}/LS_{rig}_{clip}_{METHODS[method][0]}"


def final(rig, clip, method):
    return f"{folder(rig, 'Final')}/A_{rig}_{clip}_{METHODS[method][0]}_Final"


def fk_rig(rig):
    return f"{folder(rig, 'Rigs')}/CR_{rig}_FK"


def temp_folder(rig):
    return folder(rig, "Temp")


def split(path):
    """(folder, asset name) of ``/Game/.../Name``."""
    head, _, name = path.rpartition("/")
    return head, name


def clip_of_sequence(rig, path):
    """(clip, method) of a retake sequence path, or None."""
    name = split(path)[1].split(".")[0]
    prefix = f"LS_{rig}_"
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix):]
    for method, (suffix, _label) in METHODS.items():
        if rest.endswith("_" + suffix):
            return rest[: -len(suffix) - 1], method
    return None


def baked_clips(rig):
    """{clip: Anim Sequence path} of the rig's imported clips."""
    out = {}
    prefix = f"A_{rig}_"
    for path in unreal.EditorAssetLibrary.list_assets(folder(rig, "Clips"), False):
        name = path.split(".")[-1]
        if name.startswith(prefix):
            out[name[len(prefix):]] = path.split(".")[0]
    return out


def retake_sequences(rig, methods=None):
    """[(clip, method, path)] of the rig's retake sequences."""
    out = []
    for path in unreal.EditorAssetLibrary.list_assets(folder(rig, "Retakes"), False):
        path = path.split(".")[0]
        found = clip_of_sequence(rig, path)
        if found and (methods is None or found[1] in methods):
            out.append((found[0], found[1], path))
    return sorted(out)


# ---------------------------------------------------------------------------
# Moving the flat layout of 2026-11 into this one
# ---------------------------------------------------------------------------

def organize(rig, delete_leftovers=True):
    """Move ``ROOT``'s flat assets of ``rig`` into the layout above and
    delete what no step uses any more (the checks' bakes ``*_ControlRig``,
    test leftovers ``*tmp*``). Returns {"moved": [...], "deleted": [...],
    "failed": [...]}."""
    library = unreal.EditorAssetLibrary
    result = {"moved": [], "deleted": [], "failed": []}
    prefix_a, prefix_ls = f"A_{rig}_", f"LS_{rig}_"
    for path in library.list_assets(ROOT, False):
        path = path.split(".")[0]
        name = split(path)[1]
        target = None
        if "tmp" in name.lower() or name.endswith("_ControlRig"):
            if delete_leftovers:
                (result["deleted"] if library.delete_asset(path) else result["failed"]).append(path)
            continue
        if name == f"CR_{rig}_FK":
            target = fk_rig(rig)
        elif name.startswith(prefix_ls):
            rest = name[len(prefix_ls):]
            if rest.endswith("_FK"):
                target = sequence(rig, rest[:-3], "fk_layer")
            else:
                target = sequence(rig, rest, "full_bake")
        elif name.startswith(prefix_a):
            rest = name[len(prefix_a):]
            if rest.endswith("_FK_Final"):
                target = final(rig, rest[: -len("_FK_Final")], "fk_layer")
            elif rest.endswith("_Final"):
                target = final(rig, rest[: -len("_Final")], "full_bake")
            else:
                target = baked(rig, rest)
        if target is None:
            continue
        library.make_directory(split(target)[0])
        if library.rename_asset(path, target):
            result["moved"].append((path, target))
        else:
            result["failed"].append(path)
    # Redirectors left by the moves.
    try:
        tools = unreal.AssetToolsHelpers.get_asset_tools()
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        redirectors = [a.get_asset() for a in registry.get_assets_by_path(ROOT, True)
                       if str(a.asset_class_path.asset_name) == "ObjectRedirector"]
        if redirectors:
            tools.fixup_referencers(redirectors)
    except Exception as exc:
        unreal.log_warning(f"[AnimPaths] Redirectors not fixed: {exc}")
    return result
