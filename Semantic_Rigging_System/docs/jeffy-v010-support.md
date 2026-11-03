# Murakami V010 (Jeffy rig): support plan

Status (2026-11-03): **J0 built** (Jeffy reader, export of V010). **J1
built**: in Unreal, 4 of 5 V010 clips are T0 and 1 is T1 (better than V06),
confirmed after the `Root` name fix (97 bones scored, `Root` included).
J2–J6 to do. Chosen over continuing the animation work on V06: the
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
| **J5** Splines | spine and neck: ribbon + arc-length spacing, IK/FK, stretch, wave (time) | spine and neck within T1; wave matches at several times |
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
