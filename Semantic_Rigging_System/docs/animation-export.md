# Animation export: assessment and plan

Status: **plan only** (2026-11-02). No animation code exists yet. Updated the
same day: the work is split into **two axes** that share one start
(section 5): **axis 1**, small retakes on the baked clip (route F), and
**axis 2**, the whole animation as the animator's own keys on the Control Rig
(route C), so an edit behaves as it would in Maya. This page
says what "exporting an animation" has to mean for Kotsudo, which routes are
possible, which one we take first, and how we will know it works.

The plain-language version, with figures, is the "Animation export (plan)"
section of `docs/explainer/kotsudo-explained.html`.

---

## 1. Where the tool stands today

| Area | State |
|---|---|
| Rig transfer (IK/FK, spline IK, constraints, rest correction, layout) | Runs in Unreal. Murakami: 21/21 modules within T1 on the pose check. |
| Custom attribute links, blendshapes, space switching, offset groups, materials | Built and checked in Maya and offline. **Not run in Unreal yet** (R1). |
| Pose check | In use. Maya writes `<name>.poses.json` (rest, one probe per channel, 12 random poses). Unreal replays each pose on the rebuilt rig and scores every joint. |
| Animation | Nothing yet. The rig FBX is exported at bind pose with `FBXExportBakeComplexAnimation` off. There is no Sequencer code. |

The pose check matters here: an animation is **one pose per frame**. The
pose check already proves that "these Maya control moves" give "these joint
positions" in Unreal. Animation adds two things on top:

1. doing it for every frame, and
2. storing the result as **keys an animator can edit** in Sequencer.

## 2. What "exporting an animation" has to mean

Three levels. Each matches one of the research observers (viewer, animator).

| Level | Question | Who cares |
|---|---|---|
| **Playback** | Does the character move the same, frame by frame? | Viewer (pixels) |
| **Editable** | Are the keys on the Control Rig controls, so an animator can change the motion in Sequencer? | Animator |
| **Same curves** | Are they the animator's own keys: same key times, same curve shapes? | Animator (strict) |

A baked skeleton passes level 1 only. The goal is level 2 everywhere and
level 3 wherever it can be exact.

## 3. What we already have and can reuse

* **Control list and switches**: `export_test_poses.manifest_controls()` and
  `switches()` find every animator control and IK/FK switch in the manifest.
* **World state in Unreal axes**: `export_test_poses.world_state()` (world
  matrix → Unreal position in cm + quaternion). Handles moved pivots.
* **Applying a Maya pose to the Unreal rig**: `pose_harness._posed_control()`
  (Unreal rest × Maya world delta) plus the set → solve → re-set (twice) loop
  in `pose_harness.run()`, which places controls correctly even under parents
  the rig moves (follow spaces, blended spaces).
* **Scoring**: the harness metric (motion from rest, per joint, T0/T1 tiers),
  its report and its "vs previous run" comparison.
* **Float channel meaning**: the builder already knows when a Maya value means
  something else in Unreal (IK/FK value swapped when IK = 0; ranges other than
  0..1 turned into a 0..1 slider; enums kept as 0..N-1).

## 4. The options

### A. Baked skeleton (FBX → Anim Sequence)

Maya bakes the joints every frame into an FBX; Unreal imports it as an Anim
Sequence on the existing skeleton.

* + Stock tools. Exact except for the importer. Works for any rig.
* − Not editable on the controls. Unreal's "Bake to Control Rig" could put it
  back on controls, but only for a rig with a Backwards Solve graph, and our
  generated rigs only have Forwards Solve (see E).
* **Role: the answer key and the fallback.**

### B. Per-frame control bake (Kotsudo sidecar)

Maya records every control's world transform and every channel value on
every frame. Unreal turns each frame into control values with the pose-check
math and keys them on the Control Rig track.

* + Reuses proven code. Exact on every frame (to the pose check's accuracy).
  Works whatever the control frames are, and automatically includes motion
  from parents Kotsudo does not rebuild (e.g. an animated `world_ctrl`),
  animation layers, and constraints on controls.
* − A key on every frame: editable, but not the animator's curves.
* **Role: inside the retake window (axis 1), and axis 2's fallback for the
  channels C can't copy exactly yet.**

### F. Retake window (A everywhere, B only where the animator asks)

The baked Anim Sequence (A) plays the whole clip. The animator picks a frame
range to change; only there does a Control Rig section take over, keyed from
the sidecar's per-frame control states (B, limited to the window), with a
short ease in and out at its edges.

* + No inverse problem: Unreal's own "put controls on an existing animation"
  path (E) has to work out control values from the bones, which needs a
  Backwards Solve. We don't: the sidecar already holds where every Maya
  control is on every frame, so the window starts from Maya's own pose.
* + Outside the window the animation stays exact (it is the baked clip).
* + Small edges: at the window's edges the rig pose matches Maya to the pose
  check's accuracy (T1, 0.1 cm), so the blend has almost nothing to hide.
* + Few frames, few keys: light, and the control-orientation question
  (section 14) barely matters, since the window's keys are rebuilt anyway.
* − A retake that changes where the character ends up (hips moved forward)
  does not carry into the baked frames after the window; the ease-out pulls
  back to the baked motion.
* − The edit stays in Unreal (no way back to Maya yet).
* **Role: axis 1.** It exercises the same machinery as B on a short range,
  and it is a workflow an animator can use on its own: "exact everywhere,
  editable where I asked".

### C. Key copy (Kotsudo sidecar)

Copy the Maya keys themselves (times, values, tangents) onto the matching
Unreal channels.

* Exact for **float channels** whose meaning maps by a known rule: IK/FK
  switch, space enums, sliders, blendshape sliders. A linear remap (swap,
  range change) keeps a Bezier curve exact: remap values and tangent slopes.
* Exact for **translation** when the Unreal control hangs in the same parent
  as in Maya: a fixed linear map keeps curves exact, but mixes axes, so keys
  land on the union of the three channels' key times.
* **Rotation is the hard case.** Today a control takes its bone's orientation,
  not the Maya controller's (`controller-export.md`, "Controller orientation
  is not carried"). The same rotateX number then means a different motion, and
  Euler curves do not convert by a linear rule. Two cases:
  * **Axes equal up to a relabel** (a signed axis swap): the Euler curves
    convert exactly by renaming channels and flipping signs, with the rotate
    order relabelled the same way and set on the control
    (`PreferredRotationOrder`, Unreal 5.6). On Murakami this covers 40 of 49
    controls (19 identical, 21 relabelled).
  * **Axes really different** (Murakami: 9 controls, leg IK and foot ≈ 11°,
    neck 3–5 ≈ 22°, eyes 30°): either the control carries the Maya
    controller's orientation (builder change, then case 1 applies), or keys
    at the Maya key times with values from the per-frame samples, extra keys
    only where the error passes a tolerance (approximate).
* **Tangents** are converted, not trusted: Maya's `auto`/`spline` and
  Unreal's `Auto`/`SmartAuto` don't compute the same slopes, so every key
  gets explicit (user, broken if Maya's are) tangents with Maya's slopes in
  value per frame and, for weighted curves, Maya's weights. Pre/post
  infinity (constant, linear, cycle, oscillate) maps to the channel's
  extrapolation.
* **Animated parents Kotsudo doesn't rebuild** (`world_ctrl`): a child's
  local keys then miss the parent's motion. When such a parent is animated,
  build it as a control (preferred) or fall back to B for its children, and
  report it.
* **Role: level 3, channel by channel, where it is exact. The main route of
  axis 2.**

### D. Unreal's "Import FBX onto Control Rig"

`ControlRigSequencerLibrary.import_fbx_to_control_rig_track` reads FBX node
curves and maps node names to control names, with per-channel axis mapping and
negation (`UMovieSceneUserImportFBXControlRigSettings`).

* + Stock tool.
* − Copies local channels. Same limit as C: only right when frames differ by
  an axis swap. Moved pivots and unexported parents are not handled.
* **Role: a stock comparison for the research, not the main route.**

### E. Unreal's "Bake to Control Rig"

`ControlRigSequencerLibrary.bake_to_control_rig` evaluates an animation and
asks the rig's Backwards Solve for control values.

* − Needs a Backwards Solve per module (FK is easy, IK and spline are not).
  Our rigs have none.
* + Would put any baked animation (mocap, other tools) on the controls.
* **Role: later.** Not needed while the animation comes from the Maya rig.

### Summary

| Route | Playback | Editable | Same curves | Work |
|---|---|---|---|---|
| A baked skeleton | exact | no | no | small |
| B per-frame bake | ≈ pose check (T1) | yes, a key per frame | no | medium |
| B + key reduction | within a set tolerance | yes, fewer keys | partly | small on top of B |
| F retake window | exact outside, ≈ T1 inside | yes, in the window | no (rebuilt keys) | small–medium |
| C key copy | ≈ pose check | yes | yes where exact | medium–large |
| D stock FBX→Control Rig | to measure | yes | only if frames align | small |
| E Bake to Control Rig | to measure | yes | no | large |

## 5. Two axes, one shared start

**Shared start** (both axes need it):

1. One Maya export writes the baked FBX (A) and the sidecar.
2. Unreal plays the baked clip in a Level Sequence, and the **animation
   check** scores it frame by frame against the sidecar's Maya joints (the
   measuring stick: ≈ 0).

**Axis 1: small retakes on a baked animation.** The animation stays baked;
the animator fixes a few frames with the rig (route F). Questions it answers:
can the controls take over mid-clip without a jump, and is that enough for
everyday fixes? Stock comparison: Unreal's FK Control Rig as an additive
layer on the same baked clip (works today, but FK on bones only: no IK,
spline or sliders).

**Axis 2: the animation lives in the Control Rig.** The whole clip arrives
as keys on the controls, so a change has the effect it would have in Maya:
moving one key bends the curve smoothly between its neighbours, and the rest
of the clip follows. That only holds with the animator's own sparse keys
(route C). With a key on every frame (B), moving one key makes a one-frame
spike, which is not how a Maya retake behaves. So axis 2 is built on C, with
B only where C can't be exact yet. Questions it answers: how many channels
can be copied exactly, and does the same edit give the same result in both
programs (the **edit-equivalence test**, section 10)?

The two axes don't depend on each other after the shared start, so they can
be worked on in parallel (separate branches).

## 6. Challenges and how each is handled

| # | Challenge | Handling |
|---|---|---|
| 1 | Unreal controls are oriented like their bone, not like the Maya controller: the same channel number means a different motion. | B works in world deltas, not channel numbers. C only copies where the mapping is exact. |
| 2 | Parents Kotsudo does not rebuild (`world_ctrl`) can be animated. | B samples world transforms, so their motion is included. C must detect it and fall back to B for that control. |
| 3 | Controls under rig-driven spaces (follow spaces, blended spaces). | Reuse the harness loop: set, solve, re-set twice, then read the control value. |
| 4 | Euler flips: a quaternion has many Euler forms; per-frame conversion can jump by 360°. | Key Euler values ourselves (`set_local_control_rig_euler_transforms`), choosing each frame's form closest to the previous frame. |
| 5 | Time: frame rate, start/end, sub-frame keys. | Read Maya's time unit and playback range; set the sequence display rate and playback range (end exclusive in Unreal: `end + 1`). Sub-frame keys use tick-resolution time. |
| 6 | Units: key values and tangents come in Maya's UI units. | Linear values × `_maya_linear_to_centimeters_scale()`; angles stay degrees; tangent slopes scaled the same way as values. |
| 7 | Float channels whose meaning changed (IK/FK swap, range → 0..1). | One remap per channel, from the manifest, applied to values and slopes. |
| 8 | Animation layers, constraints and pair blends on controls: the plug is not one curve. | Detect them; those channels are per-frame only (B). |
| 9 | Driven keys are also anim curves. | Time-driven curves are `animCurveT*` (TA, TL, TT, TU). Driven keys are `animCurveU*` and are already handled as rig behaviour. Never mix the two. |
| 10 | Editable vs faithful. | Report both: error per frame and keys per channel vs Maya. |
| 11 | Retake window edges: a jump where the baked clip hands over to the rig. | Start the window from Maya's own pose (sidecar), ease in and out over a few frames, and measure the jump at both edges. |
| 12 | A retake that moves the character (root, hips) can't carry into the baked frames after it. | Reported as a limit; later option: offset the rest of the clip by the change at the window's end. |

## 7. Files written per clip

Next to the rig FBX, one pair per clip:

* `<name>.<clip>.anim.fbx`: baked joints (route A). Root joint hierarchy,
  plus the blendshape meshes if any, so morph curves come along.
  `FBXExportBakeComplexAnimation -v true`, `...Start/End` = clip range,
  `...Step 1`, skins/shapes as the rig export.
* `<name>.<clip>.anim.json`: the sidecar (routes B and C, and the check).

```jsonc
{
  "schema": "kotsudo.anim", "schema_version": 1,
  "rig_name": "murakami", "manifest_schema_version": 6, "clip": "walk",
  "time": {"fps": 24.0, "maya_unit": "film", "start": 1, "end": 48},
  "units": {"linear": "cm", "rotation": "quat_xyzw_world"},
  "control_rest": { "...": "same as poses.json" },
  "switches":     [ "...same as poses.json..." ],
  "controls": {
    "L_ArmFKctrl_01": {
      // Unreal-axes world state on every frame (route B, the check)
      "samples": [[[x, y, z], [qx, qy, qz, qw]], "..."],
      // the Maya keys of time-driven curves (route C); absent if layered
      "keys": {
        "rotateX": {
          "unit": "deg", "weighted": false, "infinity": ["constant", "constant"],
          "keys": [[1.0, 0.0, "auto", "auto", 0.0, 0.0, 1.0, 1.0], "..."]
          //        time, value, in type, out type, in angle, out angle, in weight, out weight
        }
      },
      "source": "keys"   // or "layered", "constrained", "parent_animated"
    }
  },
  "channels": {
    "L_Arm_Switch.IK_FK": {"samples": [1.0, 1.0, 0.0, "..."], "keys": {"...": "..."}}
  },
  "blendshapes": {"Head_BS_Smile": [0.0, 0.1, "..."]},
  "joints": {"L_ArmLeaf1": [[[x, y, z], [qx, qy, qz, qw]], "..."]}
}
```

Size for Murakami (≈ 40 controls, ≈ 100 joints, 240 frames): ≈ 2–3 MB.

## 8. Maya side

New module `tools/maya/export_animation.py`, and an **Animation** tab in the
tagger tool (clip name, frame range defaulting to the timeline, "baked FBX"
and "controller keys" checkboxes, Export).

1. Read the manifest from the root joint (as `export_from_scene()` does).
2. Controls = `manifest_controls()`; switches = `switches()`.
3. For each control channel, follow its input: one `animCurveT*` gives keys
   (`keyframe -q -timeChange -valueChange`, `keyTangent -q` for types,
   angles, weights, `weightedTangents`); an anim-layer blend node, constraint
   or pair blend marks it per-frame only.
4. Step the timeline over the range (`currentTime`, refresh suspended) and
   capture with `world_state()` / `_switch_channels()` / blendshape weights /
   every exported joint, exactly as a pose capture.
5. Restore the current time. Write the json, then the baked FBX.

Offline test: a synthetic scene with keys, layers and a driven key; check
that keys are read only from `animCurveT*`, and that frame N of the sidecar
equals a pose capture at frame N.

## 9. Unreal side

New module `rig_builder/anim_import.py` and entry script
`run_anim_import.py` (same reload pattern as `run_rig_builder.py`).
The functions below exist in the 5.6 headers (`ControlRigSequencerEditorLibrary.h`,
Python name `unreal.ControlRigSequencerLibrary`); they still need a first run.

1. **Find the clip files** with the search used for the poses file.
2. **Route A**: import the baked FBX as an Anim Sequence on the rig's
   skeleton. UE 5.6 imports through Interchange, which ignored the legacy
   `FbxImportUI` options for the shape meshes, so this needs the Interchange
   pipeline settings (animation only, skeleton set).
3. **Level Sequence** `/Game/RigBuilder/Anims/LS_<rig>_<clip>`: display rate =
   Maya fps, playback range `[start, end + 1)`, the skeletal mesh actor bound
   at the origin, opened in Sequencer
   (`LevelSequenceEditorBlueprintLibrary.open_level_sequence`).
   `find_or_create_control_rig_track(world, sequence, rig_class, binding)`,
   then the rig instance from `get_control_rigs(sequence)`.
4. **Route B**, on a separate rig instance (the harness `RigInstance`): for
   each frame, reset, apply every control's `_posed_control()` target and the
   channel values, run the set → solve → re-set loop, then read each
   control's value (offset removed: the number Sequencer keys). Convert to
   continuous Euler and write one batch per control with
   `set_local_control_rig_euler_transforms(sequence, rig, name, frames, values)`.
   (Simpler but slower alternative: `set_control_rig_world_transforms`,
   parents first, letting Sequencer solve each frame.)
5. **Route C for floats**: `set_local_control_rig_floats` at the Maya key
   times, then set interpolation and tangents on the section's float channel
   keys. Animation channels are resolved by their element name in the
   hierarchy (the engine may suffix duplicates, e.g. `IK_FK_2`), as the
   harness does.
6. Optional key reduction: `ControlRigSequencerLibrary.smart_reduce` on the
   section, with a tolerance.
7. **Route F (retake window)**: an animation track plays route A's Anim
   Sequence over the whole clip. The Control Rig section is limited to the
   window plus its ease frames, keyed by step 4 over that range only, with
   section easing in and out. Entry point: `run_anim_import.py` with
   `RETAKE_RANGE = (start, end)` and `EASE_FRAMES` (`None` = whole clip,
   route B). To verify: how a non-layered Control Rig section's easing blends
   with the animation track under it in 5.6, and whether the Layered Control
   Rig mode is the better host for the window.

## 10. How we will know it works: the animation check

New `rig_builder/anim_check.py`, run after the import.

1. **End to end**: export the Level Sequence to an Anim Sequence
   (`export_anim_sequence_from_sequencer`), read every bone on every frame
   (`AnimPoseExtensions.get_anim_pose_at_frame`, then `get_bone_pose(...,
   WORLD)`), and compare with the sidecar's Maya joints using the pose-check
   metric (motion from rest, per joint, T0/T1).
2. **Measuring stick**: run the same comparison on route A's Anim Sequence.
   It should be ≈ 0; if not, the comparison is wrong, not the rig.
3. **Editability numbers**: keys per channel in Unreal vs in Maya, channels
   copied exactly (C) vs baked (B), channels skipped.
4. **Report** next to the clip: verdict line, per-module tiers, error per
   frame (a small graph in the HTML), worst frames with their worst joints,
   and "vs previous run", like the pose report.

For route F, three extra numbers: frames outside the window must equal the
baked clip (≈ 0); the window's frames, with the controls left untouched,
within T1; and the jump at each edge (the largest frame-to-frame change
across the ease, against the same change in Maya).

For axis 2, two more:

* **Key fidelity**: per channel, Unreal key count and times against Maya's,
  and the curve between keys (evaluated at half frames) against Maya's
  curve. A copied channel should match to numerical precision.
* **Edit-equivalence test**: the same scripted edits are applied to the
  same keys in Maya and in Unreal (move a key's value, shift a key in time,
  change a tangent, delete a key). Maya re-exports the edited clip's joints;
  Unreal plays the edited sequence; the check compares the two. This is the
  direct measure of "editing in Unreal is like retaking in Maya".

Done for axis 1 when: the measuring stick is ≈ 0, an untouched window is
within T1 on every frame, and neither edge adds a visible jump. Done for
axis 2 when: every module of every test clip is within T1 on every frame,
the exactly copied channels match Maya's curves, and the edit-equivalence
test is within T1.

## 11. Test clips (Murakami)

Generated by a script (`tools/maya/test_animations.py`, seeded), so they are
repeatable:

1. FK body motion only (modules already verified in Unreal).
2. IK/FK switch keyed mid-clip; both chains keyed.
3. Spline spine and head.
4. Animated `world_ctrl` (a parent Kotsudo does not rebuild).
5. Stress: a 360° spin (Euler flip), sub-frame keys, a non-default rotate
   order, an anim layer.
6. After R1: a space switch keyed mid-clip, petal and blendshape sliders.

Later, at least one clip made by an animator, not by a script.

## 12. Phases

**Shared start**

| Phase | Delivers | Done when |
|---|---|---|
| **S0** Maya clip export | `export_animation.py`, Animation tab, baked FBX + sidecar (with sparse keys and tangents), offline test | sidecar frame N = pose capture at frame N; keys read only from `animCurveT*` |
| **S1** Unreal baseline | baked FBX → Anim Sequence in a Level Sequence; animation check on it | measuring stick ≈ 0 |

**Axis 1: retakes on the baked clip**

| Phase | Delivers | Done when |
|---|---|---|
| **1a** Retake window (F) | Control Rig section over a chosen range, per-frame keys (B) and float keys (C) in it, ease in/out | untouched window within T1; outside = baked; no visible edge jump |
| **1b** Stock comparison | Unreal's additive FK Control Rig on the same clip and range | the same three numbers, side by side |

**Axis 2: the animation as keys in the Control Rig**

| Phase | Delivers | Done when |
|---|---|---|
| **2a** Exact key copy | float channels; translation under the same parent; rotation where axes match up to a relabel (rotate order set on the control); tangents and infinity converted; B for the rest, reported per channel | test clips within T1 on every frame; copied channels match Maya's curves |
| **2b** The remaining controls | option to carry the Maya controller's orientation, compared with keys at Maya times + refinement | every Murakami control copied, or its error reported |
| **2c** Edit-equivalence test | scripted edits applied in both programs, compared | within T1 |
| **2d** Stock comparison | route D, and B + key reduction, on the same clips | numbers in the research table |

R1 (first Unreal run of the newer features) is still pending; clips 1–5 do
not depend on it, clip 6 does.

## 13. To verify on the first Unreal run

* Interchange settings for an animation-only FBX import.
* Reading a control's value with the offset removed from a `RigInstance`
  (what Sequencer keys), and that keying it reproduces the pose.
* Naming of animation channels (`IK_FK`, attributes) in Sequencer calls.
* `set_local_control_rig_euler_transforms` keeps our Euler values as given.
* Which calls need the sequence open in Sequencer.
* `export_anim_sequence_from_sequencer` with a possessable actor.

## 14. Open decisions

1. **Carry the Maya controller's orientation on the controls?** Needed for
   exact rotation key copy (level 3). It changes the gizmo axes animators see
   (they would match Maya instead of the bone). It does not change playback.
   Murakami's 49 controls, compared with their bone in the exported rest
   pose: 19 have the same axes, 21 the same axes under other names (a
   signed axis relabel, which still allows an exact key copy), 9 really
   differ (leg IK and foot ≈ 11°, neck 3–5 ≈ 22°, eyes 30°). **Decided in
   2b**, where it becomes an option compared with the animation check and
   the edit-equivalence test. Axis 1 and phase 2a don't depend on it.
2. **Test clips**: script-generated only to start, or is there an existing
   Murakami animation to include?
3. **Sequence binding**: possessable actor in a test level (simple) or a
   spawnable (self-contained sequence).

## 15. Link to the research question

"Is the transferred animation still the same animation?" splits the same way
as the rig: by observer. Route A is perfect for the viewer and useless for
the animator; B is near-perfect for the viewer and editable; C adds the
animator's own curves. The animation check measures each route for each
observer, which feeds R3 (metrics). The two axes are two answers to "what
does the animator need": axis 1 says a few editable frames on an exact clip;
axis 2 says the whole animation, editable as in Maya. The edit-equivalence
test gives axis 2 a direct, falsifiable measure.
