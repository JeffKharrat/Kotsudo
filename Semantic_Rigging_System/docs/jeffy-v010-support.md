# Murakami V010 (Jeffy rig): support plan

Status (2026-11-10): **J0 built** (Jeffy reader, export of V010). **J1
built**: in Unreal, 4 of 5 V010 clips are T0 and 1 is T1 (better than V06),
confirmed after the `Root` name fix (97 bones scored, `Root` included).
**J2 built** (FK chains, spaces, `length`, rig base, face-slider wires):
the Unreal build runs (21 modules, 629 nodes); its pose check was first run
against the wrong poses file (fixed: the harness never borrows another
rig's poses). **J3, J4, J5 built** (limbs, ribbons, splines: all 35 setups
now reach the Unreal builder); their maths is checked against Maya by
`tests/maya_jeffy_reader_test.py`; the Unreal build and pose check of the
whole rig are next (section "J3–J5"). J6 follows that run.

Update (2026-11-12): the first full Unreal run (31 modules) calibrated at
rest to 0.002 cm, but the torso spline stopped on a pin name and every
module below it was skipped (see "J3–J5 in Unreal, first run"). Fixed,
with every node and pin of the Jeffy modules now checked against the UE
5.6 headers. The pre-export check knows what the Jeffy modules rebuild:
V010 analyses with 0 errors, 0 to review.

### J3–J5 in Unreal, first run (2026-11-10)

- Rest calibration OK: joints max 0.0019 cm, controls 0.0001 cm.
- `C_spine` (Jeffy.spline) failed: *Transform Vector*'s input is
  `Location` in UE 5.6 (the module fed `Vector` / `Value`). Every module
  under the torso (limbs, ribbons, neck, eyes, tentacles) was skipped, so
  only 9 of 87 controls existed and no pose could be applied.
- Found by checking all units offline (engine headers, not a run): Aim
  Math is `RigUnit_AimBoneMath` with `InputTransform` (the module asked
  for `AimMath` / `Input`, the next failure on the ribbons). Every other
  unit and pin used (float / vector / quaternion / transform maths, Get /
  Set Transform, control visibility, the four spline units, Basic IK's
  `BoneALength` / `BoneBLength` / `PoleVector`) matches the headers.
- Wave time: UE 5.6 has no node for the sequence's absolute time
  (Timeline and Get World Time count playback / world seconds, which do
  not follow Sequencer scrubbing). The wave is rebuilt frozen at the
  exported frame; amplitude, length, offset and falloff still act, only
  `waveSpeed` does not animate it. The pre-export check shows this as a
  gray "Partly transferred" note.
- Pre-export check: limb `upperLength` / `lowerLength` / `softness` /
  `stretch` / `pin` / `twist`, spline `stretch`, the wave attributes and
  `globalScale` are no longer "Not transferred" (the Jeffy modules rebuild
  them). Offset groups placed by a matrix network that a module rebuilds
  (ribbon bend groups, spline FK zeros carrying the wave, settings groups
  following the limb end) are marked `module` with what places them
  instead of "placed by a matrix network".

### J3–J5: limbs, ribbons, splines

All of V010 is now described for the Unreal builder. The rule stays the
same as J2: the controls are standard Kotsudo records (parents, spaces,
driven groups, attributes), and what a record cannot carry -- the maths
of a setup -- is **measured** from the scene into the module's `params`
(`limb`, `ribbon`, `jeffy_spline`), then rebuilt as float / vector nodes
(`rig_builder/jeffy_math.py`).

**J3 `ikfk_limb` → `Jeffy.ikfk_limb`** (`modules/jeffy_modules.py`):
Kotsudo's IK/FK module (FK controls, Basic IK, the switch control) with
Jeffy's IK goal in front of the solver: the effector is not the IK control
but `goal` from `ikfk_limb._ik_goal` (soft IK, stretch, pin), the bone
lengths follow `upperLength` / `lowerLength` × stretch factor, pinned to
the pole distances by `pin`, and `twist` turns the pole position about the
start→end axis (Maya +30° measured as +30° right-handed; Unreal's mirrored
axes get −twist). The FK root's *orient* space keeps its translation on
the first space (the chest / pelvis output), as Jeffy's blendMatrix does.
`ikFk` proxies on the limb controls, `autoSnap` and the `sep_*` separators
are dropped from the records (one switch, on the settings control, which
follows the end joint).

**J4 `ribbon` → `Jeffy.ribbon`** (`modules/jeffy_ribbon.py`, solve stage
"helpers", after the limb): no NURBS surface is built. A point of a degree
3 × 1 strip at fixed (u, v = ½) is a fixed weighted sum of its 5 column
positions (Cox–de Boor weights, computed once in Maya), its tangent the
derivative weights, its width direction the same weights on each column's
rotated half-width vector. The pin frame is Aim Math on those two
directions (which pin axes they are is fitted at rest), and the joint is
`(joint_rest · pin_rest⁻¹) · pin`. The columns are the expressions Jeffy
wires (`ribbon.py`): the limb bones, an aimMatrix (first segment, roll from
the parent output), the bend controls and blendMatrix halves of those, read
from the scene as a small expression tree. The bend controls hang from
nulls re-written every frame from their follow blend. `bendyVis` shows and
hides them. Verified in Maya: the predicted joints equal the real ones at
rest and posed (see the test's "ribbon maths vs Maya" line).

**J5 `spline` → `Jeffy.spline`**: the 7 columns ride on the IK controls
(constant local offsets), so a Control Rig spline through the column
centres (B-spline mode, clamped uniform knots = Maya's) is the ribbon's
middle curve. Finding: Maya's spline IK places each hidden joint on the
curve at **chord** distance (its rest bone length × the stretch factor)
from the previous one, not at arc length -- so each joint's U is found by
a 3-step Newton chord walk on the spline (position nodes), starting from
its rest U; the stretch factor is `lerp(1, length / rest length, stretch)`.
Position and tangent come from the spline nodes; the width direction is a
second spline through the strip's edge CVs (centre + rotated half width)
minus the middle one, at the same U -- the rest basis weights were tried
first and left 0.6–2.5 cm on the posed neck (the strip twists fast near a
rotated control), the edge spline brings it to 0.0014 cm. Then Aim Math
and the rest offset as for J4. Chain mode (the neck): the result blends with the FK controls'
transforms by `ikFk`, and the **wave** turns each FK zero about its own
axis by `sin(fraction / waveLength · 360 − time · waveSpeed + waveOffset) ·
waveAmplitude · lerp(1, fraction, waveFalloff)` (frozen at the
export frame: UE 5.6 has no absolute-time node, see the first-run notes).
Not rebuilt: `volume` (film-only joint scale; the pose check ignores scale)
and the spline IK's straight overshoot past the curve end (the last joint
clamps to the end, as Maya's closest point does).

Also: open attribute ranges (`twist`, `length` with no maximum) become
unlimited channels (they were clamped to 0..1), and the pose exporter
probes such attributes by +1 instead of jumping to their minimum.

### J2: how the Jeffy setups reach the Unreal builder

The Jeffy reader now describes the controls of `fk_chain` and `rig_base` as
**standard Kotsudo controller records** (`bone_controllers`), so the
existing FK, space and driven-group machinery rebuilds them:

* **Bone = control.** Each joint copies its output, and an FK output is its
  control's world matrix (`OUT_MM = CTL.world · Outputs_GRP⁻¹`), so the
  control that places an output is that joint's `bone_driver`. `rig_base`:
  `Root` copies `C_root_CTL`; `C_global_CTL` drives no joint.
* **Parents.** The Jeffy controls above a control, up to its space group
  (Kotsudo's own walk stopped at Jeffy's matrix-driven groups).
* **Spaces.** The anchor's choice node (`space` enum → `offset · target.world`)
  becomes a blended space: one target per space (an output → its joint, a
  control → that control), weight = (`space` == i), and the control's pose
  with each space alone, measured in Maya. Targets whose setup is not
  rebuilt yet (cog, chest: J5) hold still, with a warning; the others
  work.
* **`length`.** The next control's zero group is placed by a composeMatrix
  whose translate is `rest × length`: a driven offset group (mul op), which
  the builder already rebuilds every frame. The attribute walker now
  follows links made on a whole compound plug (`md.output →
  compose.inputTranslate`).
* **`globalScale`.** Scales the global control every evaluation (Set Scale);
  everything under it follows, as in Maya.
* **Face sliders.** The 4 `wire_attribute` setups are `remapValue` nodes
  (0–10 → 0–1, clamped): the walker now reads a default-curve
  `remapValue` as a clamped remap, and blendShapes driven by a Jeffy wire
  count as registered. Their control `C_Spine5IK3_CTL` (a neck control,
  J5) is built as a settings control until then: its sliders work, it moves
  no joint yet.
* Unreal: `Jeffy.fk_chain` is the FK module on these records;
  `Jeffy.rig_base` builds the global control, wires `globalScale`, then the
  root control and `Root` (solve stage "spaces", built first). Chosen over continuing the animation work on V06: the
animation axes resume on V010 once it rebuilds in Unreal (phase J6).

### J1 so far (Maya side)

* The test clips (`tools/maya/test_animations.py`) exist for both rigs:
  `CASTS` names, per rig, the controls each clip keys (V010: Jeffy names).
* The Jeffy reader declares each setup's **IK/FK switch** in the manifest
  as tagged rigs do (`params.switch`: the settings control's `ikFk`, 1 = IK,
  0 = FK, measured on V010). The clips and the pose check now find the 5
  switches (4 limbs, neck); test poses: 664.
* `tests/maya_anim_export_test.py` on V010: all 5 clips exact against Maya
  (samples 0.000000 cm, rest unchanged, Hermite rebuild ≤ 0.000002). The
  global control `C_global_CTL` is an animator control on V010 (V06's
  `world_ctrl` was not), so its keys are exported as such; the `Root`
  joint carries the 60 cm world move.
* `tests/maya_export_rig_and_clips.py` writes a rig and its 5 baked clips in
  one folder: V010 → `FBXs/murakami_v010/`.
* Unreal: `run_anim_import.py` takes the skeleton from a Skeletal Mesh
  selected in the Content Browser when no Control Rig is open (the builder
  has nothing to rebuild on V010 yet), and skips clips made for another
  skeleton.

### J1 in Unreal (first run, 2026-11-03)

| Clip | Verdict | Worst (96 bones × 49 frames) |
|---|---|---|
| fk_body | T0 | 0.0013 cm / 0.0091° |
| ikfk_switch | T0 | 0.0054 cm / 0.0084° |
| spine | T0 | 0.0095 cm / 0.0003° |
| stress | T1 | 0.0107 cm / 0.0008° |
| world | T0 | 0.0020 cm / 0.0003° |

V06 for comparison: fk_body T0 (0.0011 cm), world T1 (0.0486 cm, the
rest-vs-bind leg offset). On V010 the world clip is T0: the matrix rig's rest
pose is its bind pose. The largest errors sit at the end of the long arm
chain (`L_ArmLeaf3` and its ribbon joints, ~0.01 cm on a 13 m character),
with a first-frame offset of the same size in some clips; stress crosses
the T0 line (0.01 cm) by 0.0007 cm. Not chased further: it is 1e-5 of the
character's height.

**Found and fixed: `Root` renamed `Root1` in Unreal.** V010's top group is
called `ROOT` (it holds `SKELETON`, `GEO`, `Blendshapes`); the FBX keeps the
meshes' parent groups, and Unreal's FBX importer compares names without
case, so it renamed the joint `Root` to `Root1`. The baked check still
passed (the bone is compared by name, and `Root` was simply left out), but a
Control Rig needs the real name. The export now renames, for the export
only, any mesh **or parent group** whose name matches a joint ignoring
case (`ROOT` → `ROOT_Mesh`), and the FBX check after export flags names
that differ only by case. Re-imported and re-checked: the bone is `Root`,
97 bones are scored (96 before: `Root` was missing), verdicts unchanged.

### J0 result (Maya test `tests/maya_jeffy_reader_test.py` on V010)

* 35 setups read (20 fk_chain, 4 ikfk_limb, 4 ribbon, 2 spline, 1 rig_base,
  4 wire_attribute), 87 controls; root `Root` (from `rig_base`).
* 31 modules (`Jeffy.<type>`, chain = driven joints in output order); the 4
  wires stay in the manifest's `jeffy` section. No joint in two modules.
* Full export: FBX verified, 78/78 control shapes, manifest valid with a
  `jeffy` section (87 control records, each a Kotsudo controller snapshot);
  the pose check finds all 87 controls; 663 test poses.
* A clip keyed on Jeffy controls exports; its samples equal a fresh capture.
* **Rig finding:** 6 skinned joints are driven by no setup and simply follow
  their parent: `L/R_ArmLeaf4`, `L/R_Cheek` (helpers in V06 too) and
  `L/R_LegLeaf4` (the foot, which had its own control `L_FootFKctrl` in V06).
* The Unreal builder skips the Jeffy modules with one line ("Jeffy setups
  not rebuilt yet") until J2–J5 add them.

Code: `tools/maya/jeffy_reader.py`; `export_rig_manifest.root_joint()` /
`manifest_plug()` (root no longer has to be named `root`); the auto-detection
walk uses long names (mesh `Head` vs joint `Head`); the tagger tool's
`scene_modules()` lists Jeffy setups when the scene has no Kotsudo tags.

Sources: the scene `AutodeskMaya/Murakami_LightCharacter_V010.ma` (analysed
in Maya 2026; it is saved in 2027) and the Jeffy toolkit's code
(<https://github.com/JeffKharrat/Jeffy-Toolkit>, commit `d1f65b3`, read, not
run).

---

## 1. What changed from V06

V010 is Murakami re-rigged with Jeffy ("Jeff Inventory" 2.0): same bind
skeleton idea and same size (head at 1338 cm; the scene is now in cm
instead of m), but every behaviour is rebuilt with matrix nodes.

| | V06 | V010 |
|---|---|---|
| Constraints / pairBlends / driven keys | 57 / 12 / 4 | 0 / 0 / 0 |
| Matrix nodes (multMatrix, blendMatrix, compose, decompose) | 6 | 890 |
| Skinned joints | 61 | 102 (40 ribbon joints on the limbs) |
| Top joint | `root` | `Root` (then `Pelvis`) |
| Space switches | 0 | 37 (enum → choice → multMatrix) |
| Module tags (`rigTag_*`) | yes | none |
| Setup description | none | 35 Jeffy `*_SETUP` network nodes |

Today Kotsudo finds **0 modules and 0 controls** in V010 (it looks for
`root`, `rigTag_*` tags, ikHandles on the bind chain, constraints and
pairBlends). The Checks tab reports the stretch / pin / length attributes
(19 items) as "not transferred".

## 2. The key fact: Jeffy's contract

Every Jeffy setup ends in **outputs** (one transform per bind joint), and
each bind joint is driven so that

```
joint world matrix = output world matrix      (game safe: no stretch scale)
```

(`jeffy/setups/core/drive.py`: `local = output.world * parent.worldInverse`,
parent scale compensated, joint orient and rotate axis taken out.) Every
V010 setup is built with `game_safe` on, so bind joints never get stretch
scale: stretch moves child joints instead. That is exactly what an engine
wants.

So rebuilding V010 in Unreal means: **compute each output's world
transform with Control Rig nodes, and set the bone to it.** The bone part
is trivial (Set Transform, global); the work is in the outputs.

And Jeffy **declares** each setup in the scene (network node `*_SETUP`):

* `jeffySetupType`, name, side, version, `jeffy_options` (JSON);
* `jeffy_slots`: the joints it was built on (start, mid, end...);
* `jeffy_anchors`: its spaces (kind parent / orient / point, the space
  roles, the enum attribute, the rest matrix);
* `jeffy_rest`: the bind joints' rest channels;
* message links to its controls, outputs, systems and driven joints.

This is the "declared intent" Kotsudo otherwise asks the artist to tag by
hand. **Kotsudo reads it** (a Jeffy reader beside the tag reader), and keeps
measuring the values from the scene (rest matrices, weights) and checking
the result with the pose check, as for any rig.

## 3. Setup by setup

| Jeffy setup (V010 count) | What it computes | Unreal rebuild | Expected match |
|---|---|---|---|
| `rig_base` (1) | Global and Root controls, `globalScale` | two controls; global scale = uniform scale of everything under Global | exact |
| `fk_chain` (20: 12 petals, eyes, eyelids, toes) | one control per joint; outputs = control world matrices; `length` stretch moves the next control along the bone; spaces on the top control | Kotsudo FK, plus `length` (translate the child zero along the bone) | exact |
| spaces / anchors (37, on fk, ik, pole, spline controls) | per space: `offset · target.world` with `offset = rest · target_rest⁻¹`; the enum picks one (`choice`); orient / point keep the other half from space 0 (`blendMatrix`) | per space a constant offset × target global; select by the enum channel; orient/point: blend rotation or translation only. Replaces today's "matrix network: reported, not rebuilt" | exact |
| `wire_attribute` (4: face sliders → blendshapes) | `remapValue` linear, clamped, 0–10 → 0–1 | the existing walker (remap is supported) | exact |
| `ikfk_limb` (4) | hidden RP IK chain (pole vector, `twist`), FK controls with `length`, per-joint `blendMatrix` FK ↔ IK by `ikFk`, IK goal with **soft IK, stretch, pin, upper/lower length** (formulas in `ikfk_limb._ik_goal`) | Kotsudo IK/FK, plus the goal maths as float/vector nodes (formulas below), bone lengths from the stretched/pinned lengths, Basic IK with pole and twist, blend by `ikFk` | T1 (as V06) |
| `ribbon` (4: bendy arm/leg segments, 5 joints each) | per segment a degree-3×1 NURBS strip whose 5 CV columns ride on matrices (start, ¼, bend control, ¾, end); `uvPin` joints at fixed u; bend controls; twist "from parent" | **exact by weights**: a point at fixed (u, v) on that strip is a fixed weighted sum of the 5 column positions (B-spline basis, computed once in Maya), and so is the tangent; the joint frame is rebuilt from position + tangent + width vector. Same idea as the spline IK's weighted CVs | T0–T1 |
| `spline` (2: spine `spine` mode, neck `chain` mode) | joints ride on a hidden ribbon (`uvPin`); a hidden spline-IK "length chain" spaces them by arc length (stretch 0 keeps lengths); IK + FK controls; neck: `ikFk`, **wave** (sine of time × speed on the FK controls) | ribbon by weights (as above); arc-length spacing with Kotsudo's spline fit (Fit Chain on Spline, ≈ 0.05 cm on V06) or a measured length table; wave from the sequence time and a sine node | T1 |

Soft IK / stretch / pin, as Jeffy computes them (`ikfk_limb._ik_goal`):

```
D   = distance(start, IK end) / rig scale
L   = upper * upperLength + lower * lowerLength
sd  = clamp(softness * L * 0.25, 1e-4, ∞)
Ds  = D > L - sd ? L - sd + sd * (1 - e^-((D - L + sd) / sd)) : D
f   = lerp(1, D / Ds, stretch)
goal = lerp(start + dir * Ds, IK end, 1 - (1 - stretch) * (1 - pin))
bone lengths = [upper*upperLength, lower*lowerLength] * f, then
               lerp(…, distance to / from the pole, pin)
```

## 4. Phases

| Phase | Delivers | Done when |
|---|---|---|
| **J0** Read Jeffy | `jeffy_reader.py` (Maya): setups → module list (type, slots, options, anchors, controls, outputs); root from `rig_base`'s slot (`Root`); duplicate short names handled (mesh `Head` vs joint `Head`); controls taken from the setups (and Maya controller tags); manifest records `source_rig: jeffy`. Tool: Jeffy setups listed in the Modules tab | V010 exports an FBX and a manifest listing all 35 setups and their controls; the Maya tests still pass on V06 |
| **J1** Skeleton + baked clips | V010 skeleton in Unreal; clip export and the measuring stick on V010 | `run_anim_import.py` on a V010 clip: T0/T1 as on V06 |
| **J2** FK, spaces, wires, base | `fk_chain` (+ `length`), matrix spaces (parent / orient / point, enum), `rig_base` (+ `globalScale`), `wire_attribute` | pose check: petals, eyes, eyelids, toes, face sliders within T1 |
| **J3** IK/FK limbs | soft IK, stretch, pin, lengths, `ikFk`, pole and twist, limb spaces | 4 limbs within T1 on all probes (including the new attributes) |
| **J4** Ribbons | weighted-column ribbons, bend controls, twist from parent | the 40 ribbon joints within T1 |
| **J5** Splines | spine and neck: ribbon + chord spacing (Maya's spline IK), IK/FK, stretch, wave (time) | spine and neck within T1; wave matches at several times |
| **J6** Animation | the animation axes resume on V010 (axis 1 first) | as in `animation-export.md` |

J0 and J1 do not change the Unreal builder; J2–J5 each add one module type.
The pose check needs two additions: probes for the new attributes
(`stretch`, `softness`, `pin`, `upperLength`, `lowerLength`, `length`,
space enums), and a time for the wave (probes at several times).

## 5. Risks and things to check early

* **Maya version.** V010 is saved in 2027; tests run in 2026 here (it opens
  with warnings). Anything version-specific gets flagged.
* **`blendMatrix` rotation blending**: assumed quaternion slerp; checked by
  the pose check on `ikFk` probes at 0.5.
* **`uvPin` frame**: which surface direction is the joint's X / Y (its
  tangent and normal axes settings); read from the node, checked by J4.
* **Arc-length spacing on splines**: Unreal's fit is an approximation; if it
  is not within T1, export a length table measured in Maya.
* **Time in Control Rig** for the wave (absolute sequence time vs delta
  time) — to verify in 5.6.
* **Auto-snap**: Jeffy's IK/FK snapping runs from a Maya callback; it is an
  animator tool, not rig behaviour, and is not transferred.
* **Jeffy changes**: the reader depends on Jeffy's metadata. Its
  `jeffySetupVersion` is recorded; a version Kotsudo doesn't know is
  reported, not guessed.

## 6. Why read Jeffy's description instead of the node graph

A generic translator of the whole matrix graph (890 nodes, ribbons on NURBS
surfaces, IK handles) would be far larger, and still blind to intent (which
control is the animator's, which enum is a space). Jeffy already states the
intent, and its code documents the maths. Kotsudo's rule stays the same:
**the tool declares meaning; values are measured from the scene; the
result is checked by behaviour** (pose check). A rig without Jeffy
metadata still goes through the tag reader as before.
