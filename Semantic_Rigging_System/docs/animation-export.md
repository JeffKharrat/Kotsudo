# Animation export: assessment and plan

**Status (2026-11-20): axis 1 built as a layered workflow:**
each clip becomes a Level Sequence whose Control Rig track has a locked
base layer (Maya's control values, per frame) and an additive animator
layer. Checked on the five V010 clips; the transfer adds nothing beyond the
rig's own error. It replaces the retake window (route F, phase 1a).
Details and results: section 15. **2026-11-24:** compared with a lighter FK
layer (built, M2) and a retake window (next), section 17; every step is in
the Kotsudo menu in Unreal.

Earlier status (2026-11-02): **S0 built and tested** (Maya clip export, Animation
tab, five script-made test clips). **S1 first Unreal run**: the `world`
clip imported and checked, all 97 bones within T1 on all 49 frames (worst
0.049 cm / 0.004°). Fixed since: the import also built a skeletal mesh,
and helper chains were scored. Second run: `fk_body` **T0** (0.0011 cm,
motion length ratio 1.000002); `world` T1 (0.036 cm, legs only). Cause
narrowed down: the leg bones of the `world` clip start 0.025 cm off at
frame 0 and the character's 90° turn swings that offset (√2 × 0.025).
The baked FBX itself is exact (re-imported in Maya: 0.0001 cm on every
bone, every joint carries curves), so Unreal adds it. **Found (third
run):** the offsets equal Maya's rest-to-bind distances on those bones
(`LegLeaf2` 0.0136 cm, `LegLeaf3` 0.024 cm, from the manifest's
`rest_pose`). In the `world` clip the legs don't move relative to their
parents, and for such still bones Unreal plays the skeleton's reference
pose, which is the skin's bind pose, instead of the clip's values, when
the two are within a small tolerance. The toes (1.01 cm from bind) keep
their clip values. "Preserve Local Transform" changes nothing (identical
verdicts both ways). Consequence: on Murakami, a baked clip can show
still bones up to ≈ 0.025 cm from Maya's rest pose. The measuring stick
is therefore T0 when the bones move, and T1 (≤ 0.036 cm) when a still
offset is swung by the whole character turning. Possible fix later:
export the rig's skeleton at the rest pose instead of the bind pose
(`_restore_bind_pose`), which would change what the rest correction
does, so it is left as a recorded limit for now. The work is split into **two axes** that share one start
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

* `<base>.<clip>.anim.fbx`: route A. The same nodes as the rig FBX (root
  joint hierarchy and visible meshes, through
  `export_rig_manifest.export_skeleton_and_meshes_fbx`), baked on every frame
  of the clip (`export_fbx(..., bake_range=(start, end))`), so morph curves
  come along. Verified like the rig FBX (all manifest bones, no duplicates).
* `<base>.<clip>.anim.json`: the sidecar (routes B, C and F, and the check).

`<base>` is the rig FBX's name (the manifest's `poses_file` without
`.poses.json`). Schema as built (S0, `export_animation.ANIM_SCHEMA_VERSION`
= 1):

```jsonc
{
  "schema": "kotsudo.anim", "schema_version": 1,
  "rig_name": "murakami", "manifest_schema_version": 6, "clip": "fk_body",
  "source": {"scene": "...", "maya_version": "2026", "exported": "2026-11-02T10:45:00"},
  "time": {"fps": 24.0, "maya_unit": "film", "start": 0.0, "end": 48.0, "step": 1.0,
           "frames": [0.0, 1.0, "..."]},
  "units": {"linear": "cm", "rotation": "quat_xyzw_world",
            "channel_linear": "cm", "channel_angle": "deg", "slope": "value_per_frame"},
  "control_rest": {"L_ArmFKctrl_01": {"t": [...], "q": [...], "o": [...]}},  // as poses.json
  "switches": [{"module": "L_ArmLeaf", "control": "L_Arm_IKFK_Switch",
                "attribute": "IK_FK", "ik": 0.0, "fk": 1.0}],
  "switch_values": {"L_Arm_IKFK_Switch.IK_FK": [1.0, 1.0, "..."]},           // per frame
  "controls": {
    "L_ArmFKctrl_01": {
      "dag_path": "|...|L_ArmFKctrl_01", "rotate_order": 0,
      "samples": [[[x, y, z], [qx, qy, qz, qw]], "..."],   // world state per frame (Unreal axes)
      "parents_animated": ["world_ctrl"],                 // only when a non-control parent moves
      "channels": {
        "rotateX": {
          "source": "keys",            // static | keys | driven_keys | layered | constrained | blended | connected
          "values": [0.0, 1.2, "..."], // channel value per frame (cm / deg / plain)
          // when source == "keys":
          "curve": "L_ArmFKctrl_01_rotateX", "type": "animCurveTA", "weighted": false,
          "infinity": ["constant", "constant"],
          "key_fields": ["frame", "value", "in_type", "out_type", "in_slope", "out_slope",
                         "in_weight", "out_weight"],
          "keys": [[0.0, 0.0, "auto", "auto", 0.0, 1.8, 1.0, 1.0], "..."],
          "maya_angles": [[0.0, 12.4], "..."], "locked": [true, "..."]
        }
      }
    }
  },
  "blendshapes": {"Head_BS_Smile": [0.0, 0.1, "..."]},   // weight per frame
  "joints": {"L_ArmLeaf1": [[[x, y, z], [qx, qy, qz, qw]], "..."]},
  "notes": ["world_ctrl is animated but not an exported control; ..."],
  "fbx_file": "murakami.fk_body.anim.fbx"
}
```

**Slopes** are measured on the curve Maya evaluates (one-sided Richardson
differences at each key, in value per frame), so they are right whatever the
tangent type (auto, spline, clamped, flat, linear, fixed). On the test clips,
a cubic Hermite rebuilt from the exported keys and slopes matches Maya's
curve to 0.000002 between keys (unweighted curves). Weighted curves also
need the weights; their conversion is part of 2a.

Size for Murakami (49 controls, 97 joints, 49 frames): ≈ 0.65 MB of
sidecar, 2.1 MB of baked FBX (the meshes are included).

## 8. Maya side

Built (S0): `tools/maya/export_animation.py` and the **Animation** tab of the
tagger tool (clip name, frame range with "Use Timeline", "Baked FBX"
checkbox, Scan Keys, Export Clip). Script use:
`export_animation.export_clip_from_scene("walk", 0, 48)`.

1. Read the manifest from the root joint.
2. Controls = `etp.manifest_controls()`; switches = `etp.switches()`.
3. `scan_animation()`: for each control channel, follow its input. One
   `animCurveT*` gives keys (`read_curve`); `animCurveU*` is a driven key
   (rig behaviour, left alone); an anim-layer blend node, constraint, pair
   blend or other connection marks the channel per-frame only. Transforms
   above a control that are animated but not controls themselves (an
   animated `world_ctrl`) are reported on each control they move.
4. `control_rest()`: the pose check's rest, taken with every control channel
   and every animated parent at its default (keyed channels are set,
   captured and restored without changing time).
5. Step the timeline over the range (`currentTime`, refresh suspended) and
   capture with `world_state()`, channel values, switch values, blendshape
   weights and every exported joint, exactly as a pose capture.
6. Restore the current time. Write the baked FBX, then the json.

Test clips: `tools/maya/test_animations.py` (seeded): `fk_body`,
`ikfk_switch`, `spine`, `world` (animated world_ctrl), `stress` (360° spin,
sub-frame key, zxy rotate order, weighted and stepped tangents, an anim
layer, a keyed slider).

Tests (mayapy, on a Murakami scene copy):
`tests/maya_anim_export_test.py` (all five clips: rest, per-frame samples
against a fresh capture, keys and curve rebuild, channel sources, baked FBX)
and `tests/maya_tool_ui_test.py` (the Animation tab: scan, export).

## 9. Unreal side

**S1 as written** (`rig_builder/anim_import.py`, entry `run_anim_import.py`,
same reload pattern as `run_rig_builder.py`; not yet run):

* finds the clip (`CLIP_NAME`, else the newest `*.anim.json` next to the
  rig FBX or in the repo's `FBXs`);
* imports its baked FBX as an Anim Sequence in `/Game/RigBuilder/Anims` on
  the open rig's skeleton, with the classic FBX importer (the
  `Interchange.FeatureFlags.Import.FBX` switch is turned off for the import
  and restored: Interchange ignores the classic animation-only options);
* reads every bone on every frame (`AnimPoseExtensions.get_anim_pose_at_frame`,
  `get_bone_pose(..., WORLD)`) and compares it with the sidecar's Maya
  joints: motion since the first frame, position (cm) and rotation (deg),
  per module with T0/T1 tiers, plus the first frame's positions as a
  calibration. Log: a `VERDICT (baked clip)` line and the worst joints.
  Report: `<base>.<clip>.anim_check.json` next to the sidecar.

The steps below are the plan for the later phases.
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
| **S0** ✅ Maya clip export | `export_animation.py`, Animation tab, baked FBX + sidecar (with sparse keys and tangents), Maya test | sidecar frame N = pose capture at frame N; keys read only from `animCurveT*` (passed on 5 clips) |
| **S1** ✅ Unreal baseline | `run_anim_import.py`: baked FBX → Anim Sequence; measuring stick on it. The Level Sequence comes with 1a | measuring stick ≈ 0: V06 4×T0 + 1×T1 (2026-11-02), V010 4×T0 + 1×T1 (2026-11-03) |

**Axis 1: retakes on the baked clip**

| Phase | Delivers | Done when |
|---|---|---|
| **1a** ✅ Layered workflow (replaces the retake window F) | locked base layer of per-frame control keys (B) + additive animator layer; section 15 | five V010 clips: transfer error ≤ 0.004 cm over the rig's own; layer adds as expected (2026-11-20) |
| **1a+** ✅ Exact final export | the baked clip + (rig with the layer − rig without it), section 15 | empty layer = the bake (T0); a +10° head edit lands exactly, untouched bones equal the bake (2026-11-20) |
| **1b** Retake methods compared | full control bake vs FK layer vs retake window, section 17 | the section 17 table on three clips |

**Axis 2: the animation as keys in the Control Rig**

| Phase | Delivers | Done when |
|---|---|---|
| **2a** Exact key copy | float channels; translation under the same parent; rotation where axes match up to a relabel (rotate order set on the control); tangents and infinity converted; B for the rest, reported per channel | test clips within T1 on every frame; copied channels match Maya's curves |
| **2b** The remaining controls | option to carry the Maya controller's orientation, compared with keys at Maya times + refinement | every Murakami control copied, or its error reported |
| **2c** Edit-equivalence test | scripted edits applied in both programs, compared | within T1 |
| **2d** Stock comparison | route D, and B + key reduction, on the same clips | numbers in the research table |

R1 (first Unreal run of the newer features) is still pending; clips 1–5 do
not depend on it, clip 6 does.

**Reference rig (2026-11-10).** The animation axes continue on Murakami
**V010** (the matrix rig built as a setup rig, `docs/setup-rig-v010-support.md`,
phase J6): its five clips are exported in `FBXs/murakami_v010/` and the
shared start is confirmed on it. V06 stays as the comparison rig. Phase 1a
(the retake window) starts once the V010 Control Rig passes its pose check
in Unreal (J3–J5), so that the controls the window keys are the ones the
rig will keep.

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

## 15. Layered workflow (built 2026-11-20)

The professor's suggestion: keep the baked animation as a layer and animate
on top of it. Unreal's own "Layered Control Rig" needs a Backwards Solve
(control values from the bones), which our rigs lack: V010 refuses it. The
sidecar removes the need, because Maya already says where every control is.

`run_anim_layers.py` (module `rig_builder/anim_layers.py`), per clip:

1. Imports the clip's baked FBX if missing (route A, `A_<rig>_<clip>`).
2. Per frame (route B): sets every control to its Maya target with the pose
   check's rule (`pose_harness._posed_control`), parents first, sets the
   animation channels (raw Maya values, as the pose check), then repeats
   set → solve until every control is within 0.01 cm of its target
   (`MAX_SOLVE_PASSES` 12). V010 needs up to 5 passes: the eyelids sit in
   follow spaces under the eyes, the head and the neck spline, and each pass
   settles one level. With a fixed 3 passes the eyelids were 300 cm off.
3. Reads each control's value (`get_euler_transform_from_control_value`)
   and keeps the rotation continuous: each angle ± 360°, or the flipped form
   (pitch → 180 − pitch, roll and yaw + 180), whichever is closest to the
   previous frame.
4. Makes `/Game/RigBuilder/Anims/LS_<rig>_<clip>` (rebuilt in place on a
   rerun), bound to a level actor `<rig>_Anim` (a possessable: Unreal 5.6's
   scripted spawnables are new-style bindings that `SequencerTools.export_anim_sequence`
   never spawns, "No skel mesh found in Sequencer"). Adds a Control Rig
   track and writes the base section's channels directly
   (`Ctrl.Location/Rotation/Scale.X/Y/Z`; animation channels by element name,
   `ikFk`, `ikFk_2`, ...). The V010 controls don't use a preferred rotation
   order, so `Rotation.X/Y/Z` are roll, pitch, yaw. A channel that moves less
   than 0.001 gets one key; the others one key per frame, linear.
5. Opens the sequence, adds an additive "Animator" anim layer holding every
   control (`add_anim_layer_from_selection`) and locks the base layer.
6. Checks: bakes the sequence with Unreal's own evaluation
   (`SequencerTools.export_anim_sequence`, onto a copy of the baked clip so
   the frame rate is 24, a new Anim Sequence is 30 and refuses 24) and
   compares every bone on every frame with the sidecar's Maya joints
   (`anim_import.check_anim_sequence`). Report `<base>.<clip>.layers_check.json`.

Results on the five V010 clips (rig rebuilt 2026-11-20):

| Clip | Modules within T1 | Worst | Keys (animated channels) |
|---|---|---|---|
| fk_body | 29/31 | 0.415 cm, L arm ribbon | 7,026 (128) |
| ikfk_switch | 28/31 | 0.384 cm, L elbow (`L_ArmLeaf2`) | 3,618 (57) |
| spine | 29/31 | 0.467 cm, L arm ribbon | 5,250 (91) |
| stress | 30/31 | 0.280 cm, L arm ribbon | 4,530 (76) |
| world | 29/31 | 0.565 cm, R arm ribbon | 6,354 (114) |

Measuring stick (baked clip): T0, 0.0014 cm. Sequencer's playback equals our
own solve within 0.004 cm (checked on frame 24 of fk_body), so what remains
is the rig's error. The arm ribbons are the modules the pose check puts at
up to 0.47 cm. These clips were exported on 2026-11-10: their arm bend
controls rest 0.19–0.38 cm from the current Maya rig (poses file of
2026-11-19), which matches the first-frame ribbon offsets. Re-exporting
them is the next measurement.

Layer test (fk_body, frame 24): +10° on `C_HeadFK_CTL.Rotation.X` in the
animator layer turns the `Head` bone 10.000° from the base; at layer weight
0.5, 5.000°.

**Found on the way: the setup rig's IK/FK blend.** Mid-switch (`ikfk_switch`,
ikFk 0.5) the hand was 23 cm off. Maya's setup limb blends each output
joint with a `blendMatrix` of the FK control's world matrix and the IK
joint's world matrix (position lerp, rotation slerp, per joint). The
two-bone IK node's Weight slerps the bones' world rotations and rebuilds
the positions along the chain, so the hand swings on an arc. The pose check
only probes ikFk 0 and 1, so it couldn't see this. `SetupIKFKLimbModule`
now runs the solver and the end-bone rotation at weight 1 and blends each
bone itself (`_blend_chains`): root and middle written without moving their
children (so the later reads still see the IK pose), the end bone with them.
Pose check unchanged (27/31, same four ribbons).

**Exact final export (built 2026-11-20).** `run_anim_export.py`
(`anim_layers.export_exact`) writes `A_<rig>_<clip>_Final`: Unreal bakes the
sequence twice, with the animator layers at their weights (edited) and
muted (unedited), and per bone, in its parent's space:

* translation: bake + (edited − unedited);
* rotation: bake × (unedited⁻¹ × edited), the edit turned in the bone's own
  frame, sign kept continuous between keys;
* scale: bake × edited / unedited;
* morph curves: bake + (edited − unedited).

A bone whose edited and unedited tracks differ by less than 0.0001 cm and
0.001° on every frame keeps the baked clip's own track (`SetBoneTrackKeys`
is called only for the others). Sequencer's bake has one key more than the
Maya bake (it samples the exclusive end frame); key i is frame i in both.
About 4.5 s per 49-frame clip.

Tests (fk_body):

| Animator layer | Bones rewritten | Result |
|---|---|---|
| empty | 0 of 117 | the bake itself: T0, 0.0013 cm (= measuring stick) |
| +10° on `C_HeadFK_CTL.Rotation.X` | 1 (`Head`) | Head 10.000° from the bake on frames 0, 24, 48; within 0.001° / 0.004 cm of the rig's own playback with the edit; legs, chest, arm, root equal to the bake (3e-6) |

So the rig's 0.1–0.5 cm error only reaches a bone the animator changed,
and there only as the difference between two rig evaluations, where the
rig's own error cancels except for how the edit itself moves through it.

**Fixes after the first user run (2026-11-23).**

* Editor crash in `CloseLevelSequence` (shared pointer assert) when one call
  built several clips: Sequencer was opened (layer) and closed (next clip)
  with no editor frame between. Now Sequencer is closed at most once, at the
  start, and the layers are added after the script returns, one sequence
  every few frames (`add_layers_later`, a Slate post-tick callback with a
  re-entry guard: opening a sequence ticks Slate inside the call). The log
  says "Animator layer added to N sequence(s)" when done. The final export
  mutes the layers by switching their sections off, so it never opens
  Sequencer.
* The crash also showed that the builder never saved the Control Rig: the
  restart loaded the rig of 2026-11-17. `RigBuilder.run` now saves it after
  a successful build (`SAVE_RIG_AFTER_BUILD`).
* A clip of frames 0–20 from a scene whose timeline was 0–120 imported as
  121 frames: the FBX time span is the playback range, not the bake range.
  The Maya export now sets the playback range to the clip while writing the
  FBX, and the Unreal import takes only the clip's frames (`FBXALIT_SET_RANGE`).
  The user's own clip (FirstTestClip, 21 frames): bake T1 0.0004 cm; base
  layer 0.008 cm; empty-layer final export = the bake.

Next:

* Re-export the five clips from the current Maya scene and re-measure.
* Larger edits through the limbs (an arm swung 45°): how far the ribbons'
  rig error shows in the delta.
* Space switches and `ikFk` stay on the base layer (adding them is
  meaningless); the layer holds only transform channels by use, not yet by
  construction.

## 16. Link to the research question

"Is the transferred animation still the same animation?" splits the same way
as the rig: by observer. Route A is perfect for the viewer and useless for
the animator; B is near-perfect for the viewer and editable; C adds the
animator's own curves. The animation check measures each route for each
observer, which feeds R3 (metrics). The two axes are two answers to "what
does the animator need": axis 1 says a few editable frames on an exact clip;
axis 2 says the whole animation, editable as in Maya. The edit-equivalence
test gives axis 2 a direct, falsifiable measure.

## 17. Retake methods compared (plan, 2026-11-24)

Three ways to retake a baked Maya clip on a layer in Unreal, run on the same
clips with the same edits and scored with the same numbers. Method 1 is
built (section 15); 2 and 3 are new.

### The three methods

| | **M1 Full control bake** (built) | **M2 FK layer** (lightest, built) | **M3 Retake window** (in between) |
|---|---|---|---|
| Base | Our Control Rig, keyed on every frame from the sidecar (spot method, settle loop) | The bake as keys on one FK control per bone (`CR_<rig>_FK`, generated), read straight from the bones | The baked Anim Sequence everywhere; our Control Rig keyed from the sidecar **only on the window** (+ ease frames) |
| Animator edits | Our controls (FK, IK, splines, sliders) on an additive layer | The FK controls on an additive layer | Our controls, inside the window only |
| Needs the sidecar | yes | **no** | yes, window frames only |
| Spot method runs on | every frame | no frame | window frames only |
| IK, spaces, switches | yes | **no** (FK on bones only) | yes, in the window |
| Outside the edit | rig error (≤ 0.5 cm on the arm ribbons), exact after the final export | exact after the final export (base 0.012 cm from the bake) | exact (the bake) |
| Main risk | cost: a key on every frame of every control | an IK-style edit (plant a hand) has to be done by rotating each bone | the jump at the window edges (rig pose vs bake) |

**Why M2 needs no Maya data.** An FK layer doesn't need to know where
Maya's controls were: each control *is* its bone, so the base keys are the
bones' own pose and the animator layer adds to it. That only works when the control and the bone are
the same thing, which is true for FK (rotate the forearm 10° more). For IK
an offset layer would have to know where the hand already is to move it from
there, which is the backwards problem: Unreal's Layered Control Rig solves it
with the rig's Backwards Solve (our rigs have none), and M1/M3 solve it with
Maya's spots. This is why the lightest method is FK only. Additive FK layers
are the usual everyday tool for this (MotionBuilder and Maya animation
layers, Unreal's own FK Control Rig on an animation track), so M2 is the
industry baseline to beat.

### What gets built

**M2, FK layer** (`rig_builder/anim_fk_layer.py`, `run_anim_fk_layer.py`),
**built 2026-11-24**:

1. `CR_<rig>_FK`, made once per rig from the Skeletal Mesh: one FK control
   per bone (`<bone>_FKC`, on the bone, parented like the bones) and a graph
   driving each bone from its control (Get control → Set bone, global), as
   the builder's FK module does.
2. `LS_<rig>_<clip>_FK`: a Control Rig track of that rig whose base layer
   holds the baked clip, a key per frame. The values need no solving: on a
   rig instance each control is set where the bake has its bone (parents
   first) and its value read back. Then the additive Animator layer, as in M1.
3. Final export: the same exact export as M1 (`A_<rig>_<clip>_FK_Final`).

It reads only the imported baked clip (no sidecar, no Kotsudo rig).

**Why not Unreal's stock pieces (found 2026-11-24).** The first version
played the baked clip on an animation track with Unreal's FK Control Rig on
top in layered mode. It plays when scrubbed in the editor, but
`SequencerTools.export_anim_sequence` (the bake the final export and the
checks need) gives the first frame on every frame, whatever the options
(warm-up, delay, custom mode, single-node mode, run from an editor tick).
The native `FKControlRig` loaded with the clip
(`load_anim_sequence_into_control_rig_section_with_range`) bakes frozen too.
A blueprint Control Rig track bakes correctly, so M2 uses its own FK
blueprint. Also found: a bake run while Sequencer shows *another* sequence
is frozen as well (it froze M1 once); the final export now closes Sequencer
first.

Results (V010, clips exported 2026-11-10 and 2026-11-23):

| Clip | Base layer vs the bake | Keys (animated channels of 1,053) | Build |
|---|---|---|---|
| FirstTestClip (21 frames) | 0.012 cm / 0.011° | 1,633 (29) | 24 s with the rig, 2.4 s without |
| fk_body (49 frames) | 0.011 cm / 0.011° | 9,597 (178) | 2.4 s |

Final export: empty layer = the bake (0.0 cm on both clips); +10° on
`Head_FKC.Rotation.X` in the Animator layer turns `Head` 10.000° on every
frame, only `Head`'s track rewritten, its children following.

**M3, retake window** (an option on `run_anim_layers.py`:
`RETAKE_RANGE = (start, end)`, `EASE_FRAMES`):

1. Same import and sequence as M1, plus an animation track playing the bake.
2. The Control Rig section covers only the window plus the ease frames,
   keyed by the M1 code on those frames only, with section ease in and out,
   and the additive Animator layer on top.
3. Final export: section 15's exact export, which already writes only the
   difference the layer makes, so outside the window it equals the bake.

To verify first: how a Control Rig section's ease blends with the animation
track under it in 5.6 (the base section replaces the bones, so the ease is
what hides the rig-vs-bake gap at the edges).

### The test

Clips: `FirstTestClip` (the user's own) and `fk_body` and `ikfk_switch`
(V010), after re-exporting them from the current Maya scene.

Scripted edits, the same in every method where the method can do them:

| Edit | M1 | M2 | M3 |
|---|---|---|---|
| E1 head +10° on frames 10–20 | head control | `Head` bone | head control, window 10–20 |
| E2 left arm swung 45° on frames 10–20 | shoulder FK control | upper-arm bone | shoulder FK control |
| E3 left hand moved 10 cm, held on frames 10–20, in IK | IK hand control | by hand on 3 bones (records how close and how many keys) | IK hand control |
| E4 empty layer | | | |

Numbers per method, per clip, per edit:

1. **Untouched**: with the empty layer (E4), the final clip against the bake
   (should be T0 for all three).
2. **Edit lands**: the edited bone's change against the asked change
   (10°, 45°, 10 cm).
3. **Leaks**: largest change on bones the edit should not move.
4. **Edges** (M3, and M1/M2 on the edit's first and last frames): largest
   frame-to-frame jump against the same jump in the bake.
5. **Cost**: build time, keys written, files needed (sidecar or not).
6. **Reach**: which edits the method can do at all (IK, spaces, switches,
   sliders).

Report: one table per clip in `<base>.retake_compare.json`, and a summary
table added here.

### Order

| Step | Delivers | Done when |
|---|---|---|
| **R0** | the five V010 clips re-exported from the current Maya scene; M1 re-measured | M1 numbers on fresh clips |
| **R1** ✅ | M2 built (2026-11-24) | E4 = the bake (0.0 cm); E1 lands (10.000°) |
| **R2** | M3 built | E4 = the bake outside the window, within T1 inside; edges within the bake's own jumps |
| **R3** | `run_retake_compare.py`: the four edits through the three methods, the report | the comparison table here and in the explainer |

R1 and R2 don't depend on each other and can be worked on in parallel.

### Using it by hand: the Kotsudo menu

Every step is an entry of the **Kotsudo** menu in the Unreal main menu bar
(`tools/unreal/kotsudo_menu.py`, loaded at editor start by the project's
`Content/Python/init_unreal.py`; a copy is in `tools/unreal/init_unreal.py`).
Each entry opens a small options window with the settings of its
`run_*.py` script, then runs it; results go to the Output Log.

| Menu | Script | Needs |
|---|---|---|
| Rig > Build Control Rig... | `run_rig_builder.py` | the Control Rig open |
| Rig > Pose check... | `run_pose_harness.py` | the Control Rig open, the poses file |
| Animation > 1. Import baked clips... | `run_anim_import.py` | the clips' `.anim.fbx` (+ `.anim.json` for the check) |
| Animation > 2. Retake method > M1 Full control bake... | `run_anim_layers.py` | the clips' `.anim.json`, the Kotsudo Control Rig open |
| Animation > 2. Retake method > M2 FK layer... | `run_anim_fk_layer.py` | the imported baked clips only |
| Animation > 2. Retake method > M3 Retake window | (not built yet) | |
| Animation > 3. Final export... | `run_anim_export.py` | a retake sequence (M1 and/or M2) |

A demo by hand: open the rig's Control Rig, Kotsudo > Animation > 1, then
2 > M2 (or M1), open `LS_<rig>_<clip>_FK` in Sequencer, key the Animator
layer, then 3. The scripts still run on their own (File > Execute Python
Script), with the same settings as constants at their top.
