# Build and export speed

Measured on Murakami V010 (Jeffy matrix rig, 117 joints, 87 controls,
31 modules), 2026-11-17.

## Where the hour went

| Step | Before | After | Cause |
|---|---|---|---|
| Maya: rig FBX + manifest + 635 test poses | ~60 min | 36 s | FBX check regex (below) |
| Maya: each animation clip (49 frames) | ~30 min | seconds | same regex, run once per clip |
| Unreal: graph build (31 modules) | 30 min | 4.4 min | VM recompile and hierarchy copies on every graph edit |
| Unreal: pose check (634 poses) | 2 s | 2 s | — |

### Maya: one regular expression

`verify_exported_fbx` read the node names back from the written FBX with
`([\x20-\x7e]+)\x00\x01Model`. The manifest JSON is stored inside the FBX
as one 680 KB printable run; the regex retried every start position in it
and backtracked to its end, so its time grew with the square of the run
length (20 KB: 1.6 s, 40 KB: 6.4 s, 80 KB: 26 s, 680 KB: about 31 min). It
now finds each `\x00\x01Model` tag and walks back to the start of the name
(`_fbx_model_names`): 1 ms, same names (checked on 30 FBX files).

The rest of the Maya export, profiled (`cProfile`, mayapy):

| Part | Time |
|---|---|
| 635 test poses (set a channel, read 117 joint + 87 control matrices) | 19 s |
| Describing the Jeffy setups (`build_manifest`) | 15 s |
| FBX write and shapes | 2 s |

Cheap wins left there: `jeffy_reader.read_setups()` is called 63 times
(6.5 s; cache it per export), `cmds.upAxis` 390,000 times (2.6 s; cache it
per export), `cmds.attributeQuery` 113,000 times (7.8 s), and pose reads
through `cmds.xform` (OpenMaya `MDagPath.inclusiveMatrix` halves them).

### Unreal: automatic VM recompile

Every node, link or pin value the builder set made the Control Rig
recompile its whole VM. Measured on a 2,100-node graph:

| Setting | Per graph edit |
|---|---|
| Default | 409 ms |
| Undo off | no gain |
| Auto recompile off | 46 ms |
| Auto recompile off + notifications suspended | 32 ms |

`RigBuilder.run` now turns auto recompile off for the build, turns it
back on before the final compile, and always restores it if the build
fails. The log ends with `Build time: … s`.

### Memory and crashes on repeated builds

Repeat builds ran the editor out of memory (up to 107 GiB virtual). Measured
per edit on V010: adding a graph node held 43.6 MB, removing one 14.5 MB and
adding a null 7.3 MB, all multiples of one copy of the rig hierarchy (7.3 MB).
The blueprint's change notifications copy the hierarchy to the live rig
instances, and the copies are only released when the Python call returns, so
neither garbage collection nor ending transactions freed them mid-build.

The fix is the engine's bulk-edit mode, `suspend_notifications(True)`
(`FAST_BUILD_SUSPEND_NOTIFICATIONS` in `builder.py`):

| Three builds in a row | Notifications on | Paused |
|---|---|---|
| Time per graph edit | 16 ms | 1 ms |
| Editor memory, peak | 58 GiB, then stopped | 10.4 GiB |
| Build time | 7 min | 4.4 min |

Resuming rebuilds the visual graph from the model. An open graph panel then
paints widgets of the old nodes and the editor crashes (`UStruct::IsChildOf`
in GraphEditor), so the builder closes the rig's editor tab during the build
and reopens it afterwards. Other safeguards: graph edits record no undo (a
build is undone by rebuilding), the pose check reuses the editor's rig
instance, and a build stops cleanly above 45 GiB (`BUILD_MEMORY_LIMIT_GIB`).

## Running Unreal from the command line

The project has Python remote execution on (`bRemoteExecution=True`). A
script on the same machine can run Python in the open editor with the
engine's `remote_execution.py`
(`Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python`), so a
build, a pose check or a probe of the rig no longer needs a copy-paste of
the log. Node discovery is UDP multicast and sometimes misses the first
ping while the editor is busy; retry.

## The plan for later

Ordered by gain for the effort.

1. **Build only what changed.** A module's graph depends only on its
   manifest entry and its parents' outputs. Hash each module's manifest
   entry; on a rebuild, keep the nodes and elements of unchanged modules
   and rebuild the rest. While testing one module, a rebuild drops from
   minutes to seconds.
2. **Fewer graph edits.** 3,052 nodes at about 32 ms per edit is most of
   the remaining 5.5 minutes, and each node takes several edits (add,
   defaults, links). Setting a whole struct pin's default at once instead
   of sub-pin by sub-pin, and skipping defaults equal to the unit's own,
   cuts the edit count.
3. **Shared functions instead of copies.** The four ribbons and two
   splines each place hundreds of identical maths nodes. A Control Rig
   function (one graph, called per ribbon) builds once and also makes the
   rig smaller to evaluate.
4. **Maya: cache the scene reads** listed above (about 15 s of 36 s).
5. **Pose check on demand.** It already costs 2 s; keep it in the build.
6. **Long term: a compiled solver.** The Jeffy ribbons and splines become
   hundreds of nodes because Control Rig has no strip or chord-walk node.
   A small C++ RigUnit per setup type (ribbon point, spline chord walk)
   would replace about 300 nodes with one each: faster to build, faster
   to evaluate, simpler to read. It needs a C++ plugin and a compile step,
   so it is for when the setup types are stable.
