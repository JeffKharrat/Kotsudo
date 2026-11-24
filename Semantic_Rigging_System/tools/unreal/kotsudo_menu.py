"""The Kotsudo menu in the Unreal editor: every pipeline step, run by hand.

Adds "Kotsudo" to the Level Editor's main menu bar:

    Kotsudo
      Rig
        Build Control Rig...
        Pose check...
      Animation
        1. Import baked clips...
        2. Retake method
             M1  Full control bake...
             M2  FK layer...
             M3  Retake window (next)
        3. Final export...
      Help
        Open the animation plan

Each entry opens a small options window (the same settings as the
constants at the top of the matching run_*.py script), then runs that
script's main(). Results go to the Output Log, as when the script is run
directly; errors also show in a message box. The scripts stay usable on
their own (File > Execute Python Script).

Set up once: add this file's folder to the editor's Python path and call
``register()`` at startup, e.g. a project ``Content/Python/init_unreal.py``:

    import sys
    sys.path.append(r"E:/Kotsudo/Semantic_Rigging_System/tools/unreal")
    import kotsudo_menu
    kotsudo_menu.register()
"""

import importlib.util
import os
import traceback

import unreal

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MENU_OWNER = "Kotsudo"
DOCS = os.path.join(PROJECT_ROOT, "docs", "animation-export.md")


# ---------------------------------------------------------------------------
# Steps: script, label, tooltip, options (attribute, kind, default, label)
#   kind: "bool", or "clips" (comma-separated names -> list or None)
# ---------------------------------------------------------------------------

STEPS = {
    "build_rig": {
        "script": "run_rig_builder.py",
        "label": "Build Control Rig...",
        "tip": "Rebuild the open Control Rig from the manifest in its Skeletal Mesh (open the Control Rig first).",
        "options": [
            ("RUN_POSE_CHECK", "bool", True, "Run the pose check after the build"),
            ("APPLY_MATERIALS", "bool", True, "Colour the mesh with the Maya materials"),
        ],
    },
    "pose_check": {
        "script": "run_pose_harness.py",
        "label": "Pose check...",
        "tip": "Compare the open Control Rig with the Maya poses file, pose by pose.",
        "options": [],
    },
    "import_clips": {
        "script": "run_anim_import.py",
        "label": "1. Import baked clips...",
        "tip": "Import every <rig>.<clip>.anim.fbx exported from Maya as an Anim Sequence and check it "
               "against Maya's joints. Every retake method starts from these.",
        "options": [
            ("ALL_CLIPS", "bool", True, "Every clip next to the rig FBX"),
        ],
    },
    "full_bake": {
        "script": "run_anim_layers.py",
        "label": "M1  Full control bake...",
        "tip": "The Kotsudo rig's own controls hold the clip on every frame (from Maya's control spots, "
               "needs the .anim.json), with an Animator layer on top. IK, spaces and switches editable.",
        "options": [
            ("CLIP_NAMES", "clips", "", "Clips (comma separated, empty = all)"),
            ("ADD_ANIMATOR_LAYER", "bool", True, "Add the Animator layer"),
            ("CHECK", "bool", True, "Check against Maya's joints"),
        ],
    },
    "fk_layer": {
        "script": "run_anim_fk_layer.py",
        "label": "M2  FK layer...",
        "tip": "One FK control per bone holds the baked clip, with an Animator layer on top. Lightest: "
               "needs only the baked clip. FK edits only.",
        "options": [
            ("CLIP_NAMES", "clips", "", "Clips (comma separated, empty = selected or all)"),
            ("ADD_ANIMATOR_LAYER", "bool", True, "Add the Animator layer"),
            ("CHECK", "bool", True, "Check against the bake"),
            ("REBUILD_FK_RIG", "bool", False, "Rebuild the FK rig (CR_<rig>_FK)"),
        ],
    },
    "final_export": {
        "script": "run_anim_export.py",
        "label": "3. Final export...",
        "tip": "Write the final Anim Sequence of each retake sequence: the bake plus what the Animator "
               "layer changes (untouched bones stay exactly Maya's).",
        "options": [
            ("CLIP_NAMES", "clips", "", "Clips (comma separated, empty = all)"),
            ("EXPORT_FULL_BAKE", "bool", True, "M1 sequences (LS_<rig>_<clip>)"),
            ("EXPORT_FK_LAYER", "bool", True, "M2 sequences (LS_<rig>_<clip>_FK)"),
            ("CHECK", "bool", True, "Check the result"),
        ],
    },
}

# Options that map onto one script setting in another form.
_DERIVED = {
    "final_export": lambda values: {
        "METHODS": tuple(m for m, on in (("full_bake", values.pop("EXPORT_FULL_BAKE")),
                                          ("fk_layer", values.pop("EXPORT_FK_LAYER"))) if on),
    },
}


# ---------------------------------------------------------------------------
# Options windows
# ---------------------------------------------------------------------------

_OPTION_CLASSES = {}
_OPTION_OBJECTS = {}


def _options_class(step_id):
    """A UObject class with one property per option, shown in the window."""
    if step_id in _OPTION_CLASSES:
        return _OPTION_CLASSES[step_id]
    attributes = {}
    for attribute, kind, _default, label in STEPS[step_id]["options"]:
        prop_type = bool if kind == "bool" else str
        attributes[attribute.lower()] = unreal.uproperty(prop_type, meta={"DisplayName": label})
    name = "KotsudoOptions_" + "".join(part.title() for part in step_id.split("_"))
    cls = unreal.uclass()(type(name, (unreal.Object,), attributes))
    _OPTION_CLASSES[step_id] = cls
    return cls


def _options_object(step_id):
    """The step's options, remembered between runs in this editor session."""
    if step_id not in _OPTION_OBJECTS:
        obj = unreal.new_object(_options_class(step_id))
        for attribute, _kind, default, _label in STEPS[step_id]["options"]:
            obj.set_editor_property(attribute.lower(), default)
        _OPTION_OBJECTS[step_id] = obj
    return _OPTION_OBJECTS[step_id]


def _ask(step_id):
    """Show the options window. Returns {SCRIPT_CONSTANT: value} or None (cancelled)."""
    step = STEPS[step_id]
    if not step["options"]:
        return {}
    obj = _options_object(step_id)
    options = unreal.EditorDialogLibraryObjectDetailsViewOptions()
    for prop, value in (("show_object_name", False), ("allow_search", False)):
        try:
            options.set_editor_property(prop, value)
        except Exception:
            pass
    title = "Kotsudo: " + step["label"].rstrip(".")
    if not unreal.EditorDialog.show_object_details_view(title, obj, options):
        return None
    values = {}
    for attribute, kind, _default, _label in step["options"]:
        value = obj.get_editor_property(attribute.lower())
        if kind == "clips":
            names = [n.strip() for n in str(value).replace(";", ",").split(",") if n.strip()]
            value = names or None
        values[attribute] = value
    if step_id in _DERIVED:
        values.update(_DERIVED[step_id](values))
    return values


# ---------------------------------------------------------------------------
# Running a step
# ---------------------------------------------------------------------------

def _load_script(file_name):
    """A fresh copy of the run_*.py script (so edits on disk are picked up)."""
    path = os.path.join(PROJECT_ROOT, file_name)
    module_name = "kotsudo_" + os.path.splitext(file_name)[0]
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(step_id, ask=True, **overrides):
    """Run one step: options window (if ``ask``), then the script's main()."""
    step = STEPS[step_id]
    values = _ask(step_id) if ask else {}
    if values is None:
        return False
    values.update(overrides)
    try:
        module = _load_script(step["script"])
        for constant, value in values.items():
            setattr(module, constant, value)
        unreal.log(f"[Kotsudo] {step['label'].rstrip('.')}: {values or 'default settings'}")
        module.main()
        return True
    except Exception as exc:
        unreal.log_error(f"[Kotsudo] {step['label'].rstrip('.')} failed:\n{traceback.format_exc()}")
        unreal.EditorDialog.show_message(
            "Kotsudo", f"{step['label'].rstrip('.')} failed:\n\n{exc}\n\nDetails in the Output Log.",
            unreal.AppMsgType.OK)
        return False


def open_docs():
    os.startfile(DOCS)  # noqa: S606 -- opens the plan in the default viewer


# ---------------------------------------------------------------------------
# The menu
# ---------------------------------------------------------------------------

def _entry(menu, section, name, label, tip, command, enabled=True):
    entry = unreal.ToolMenuEntry(name=name, type=unreal.MultiBlockType.MENU_ENTRY)
    entry.set_label(label)
    entry.set_tool_tip(tip)
    if enabled:
        entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, "", command)
    else:
        entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, "",
                                 "import unreal; unreal.log('[Kotsudo] Not built yet.')")
    menu.add_menu_entry(section, entry)


def _step_entry(menu, section, step_id):
    step = STEPS[step_id]
    _entry(menu, section, f"Kotsudo_{step_id}", step["label"], step["tip"],
           f"import kotsudo_menu; kotsudo_menu.run('{step_id}')")


def register():
    """Add (or rebuild) the Kotsudo menu in the main menu bar."""
    menus = unreal.ToolMenus.get()
    main_menu = menus.find_menu("LevelEditor.MainMenu")
    if main_menu is None:
        unreal.log_warning("[Kotsudo] No main menu to add to.")
        return None
    menus.unregister_owner_by_name(MENU_OWNER)
    kotsudo = main_menu.add_sub_menu(MENU_OWNER, "", "Kotsudo", "Kotsudo", "Maya rig and animation to Control Rig")

    kotsudo.add_section("Rig", "Rig")
    _step_entry(kotsudo, "Rig", "build_rig")
    _step_entry(kotsudo, "Rig", "pose_check")

    kotsudo.add_section("Animation", "Animation")
    _step_entry(kotsudo, "Animation", "import_clips")
    retake = kotsudo.add_sub_menu(MENU_OWNER, "Animation", "KotsudoRetake", "2. Retake method",
                                  "Put the clip on controls with an Animator layer for retakes.")
    retake.add_section("Methods", "Compared in docs/animation-export.md, section 17")
    _step_entry(retake, "Methods", "full_bake")
    _step_entry(retake, "Methods", "fk_layer")
    _entry(retake, "Methods", "Kotsudo_retake_window", "M3  Retake window (next)",
           "The Kotsudo rig only on the frames you retake, the bake everywhere else. Not built yet.",
           "", enabled=False)
    _step_entry(kotsudo, "Animation", "final_export")

    kotsudo.add_section("Help", "Help")
    _entry(kotsudo, "Help", "Kotsudo_docs", "Open the animation plan",
           "docs/animation-export.md", "import kotsudo_menu; kotsudo_menu.open_docs()")
    menus.refresh_all_widgets()
    unreal.log("[Kotsudo] Menu ready: Kotsudo in the main menu bar.")
    return kotsudo
