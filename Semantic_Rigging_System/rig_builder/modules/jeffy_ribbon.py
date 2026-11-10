"""Jeffy's ribbon (J4) and spline (J5) setups in Control Rig.

Both ride on a NURBS strip in Maya: five (ribbon) or seven (spline) CV
columns follow matrices, and ``uvPin`` nodes read a frame at a fixed or
computed U. Here the strip is never built: a point on a B-spline strip is
a fixed weighted sum of its columns (the basis weights, measured once in
Maya), and so are its tangent and its width direction. Each pin becomes:

    position  = sum_k  N_k(u)  * column_k.translation
    tangent   = sum_k  N'_k(u) * column_k.translation
    width     = sum_k  N_k(u)  * column_k.rotation . half_k
    pin       = AimMath(position; tangent axis -> tangent, width axis -> width)
    joint     = (joint_rest . pin_rest^-1) * pin          (joint.world = output.world)

* ``Jeffy.ribbon`` -- the bend controls follow blends of the limb's solved
  bones (their parent null is updated every frame), the columns follow the
  bones, the bend controls and blends of those (``ribbon.py``).
* ``Jeffy.spline`` -- the columns ride on the IK controls; the joints' U
  comes from Maya's spline IK: each joint sits on the curve at CHORD
  distance (its rest bone length, stretched or not) from the previous one.
  A Control Rig spline through the seven column centres gives positions and
  tangents; a short Newton chord walk finds each U (``spline.py``). In chain
  mode (the neck) the result blends with the FK controls by ``ikFk`` and a
  time-driven wave turns the FK zero groups.

The manifest data comes from tools/maya/jeffy_reader.py (``params.ribbon``
and ``params.jeffy_spline``); docs/jeffy-v010-support.md explains the maths.
"""

import math
from typing import Any, cast

try:
    import unreal  # type: ignore
except ImportError:
    unreal = cast(Any, None)

from .. import graph_utils, jeffy_math
from .rig_module import RigModule
from . import spline_ik_module

CHORD_ITERATIONS = 3          # Newton steps of the chord walk per joint
CHORD_STEP = 0.01             # U step of the secant slope
SPLINE_SAMPLES = 32


def _spline_unit(*words):
    """A ControlRigSpline unit whose name holds every word, or None."""
    for module_name in ("ControlRig", "ControlRigDeveloper", "ControlRigSpline"):
        try:
            unreal.load_module(module_name)
        except Exception:
            pass
    for attr in dir(unreal):
        if attr.startswith("RigUnit_") and all(w in attr for w in words):
            return getattr(unreal, attr)
    return None


class _Expressions:
    """Transform pins of the matrix expressions the reader exported."""

    def __init__(self, module, graph):
        self.module, self.graph = module, graph
        self.cache = {}

    def pin(self, expr):
        key = repr(expr)
        if key in self.cache:
            return self.cache[key]
        kind = expr.get("kind")
        graph = self.graph
        if kind == "bone":
            result = graph.get_transform("Bone", str(expr["joint"]))
        elif kind == "control":
            name = self.module.ue_control(expr["name"])
            if name is None:
                raise RuntimeError(f"control '{expr['name']}' is not in the rig")
            result = graph.get_control(name)
        elif kind == "lerp":
            a, b = self.pin(expr["a"]), self.pin(expr["b"])
            t = float(expr.get("t", 0.5))
            translate, rotate = float(expr.get("translate", 1.0)), float(expr.get("rotate", 1.0))
            if abs(translate - 1.0) < 1e-9 and abs(rotate - 1.0) < 1e-9:
                result = graph.tlerp(a, b, t)
            else:
                translation = f"{a}.Translation" if translate <= 1e-9 else graph.vlerp(
                    f"{a}.Translation", f"{b}.Translation", t * translate)
                rotation = f"{a}.Rotation" if rotate <= 1e-9 else graph.quat_slerp(
                    f"{a}.Rotation", f"{b}.Rotation", t * rotate)
                result = graph.make_transform(translation, rotation=rotation)
        elif kind == "aim":
            source = self.pin(expr["input"])
            target = self.pin(expr["target"])
            up = list(expr["up_vector"])
            if expr.get("up_space"):
                up = graph.rotate_vector(f"{self.pin(expr['up_space'])}.Rotation", up)
            result = graph.aim(source, list(expr["aim_axis"]), f"{target}.Translation",
                               list(expr["up_axis"]), up)
        else:
            raise RuntimeError(f"unknown matrix expression '{kind}'")
        self.cache[key] = result
        return result


class _JeffyBase(RigModule):
    """Shared: records, control names, pin frames."""

    @classmethod
    def describe_contract(cls):
        contract = super().describe_contract()
        contract["module_type"] = cls.module_type
        contract["chain"]["min_length"] = 1
        return contract

    def validate(self):
        if not self.chain or not self.context:
            raise RuntimeError(f"{self.module_type} '{self.name}' needs a chain and a context.")

    def ue_control(self, maya_name):
        key = self.context.control_key_for_maya(maya_name)
        return str(key.name) if key is not None else None

    def records(self, recipe_data):
        return graph_utils.controller_records(recipe_data)

    def record_named(self, recipe_data, maya_name):
        return next((r for r in self.records(recipe_data) if r.get("name") == maya_name), None)

    def build_control(self, recipe_data, record, parent_key, scale, color, shape_rotation=None):
        """A control from its record under ``parent_key``. Returns (name, key, driver_null)."""
        hierarchy = self.context.hierarchy
        anchor = str(record.get("driven_bone") or record.get("anchor_bone") or self.chain[0]).split("|")[-1]
        if not hierarchy.contains(graph_utils.make_key(unreal.RigElementType.BONE, anchor)):
            anchor = self.chain[0]
        bone_transform = graph_utils.get_bone_global_transform(hierarchy, anchor)
        name = self.context.control_name(record, f"{graph_utils.sanitize_name(self.name)}_"
                                                 f"{graph_utils.sanitize_name(record.get('name') or 'ctrl')}")
        key, driver = graph_utils.build_record_control(
            self.context.rig, hierarchy, self.context.hierarchy_controller, recipe_data, parent_key,
            name, anchor, bone_transform, record, graph_utils.record_color(record, color),
            "Circle_Thick", (scale, scale, scale), shape_rotation,
        )
        self.context.maya_controls[record.get("name")] = key
        return name, key, driver

    def pin_frame_pins(self, graph, columns, weights, d_weights, halves_world, frame):
        """(position, tangent, width) pins of a strip point from its column
        transform pins, basis weights and the columns' rotated half vectors."""
        position = graph.vsum([(f"{c}.Translation", w) for c, w in zip(columns, weights)])
        tangent = graph.vsum([(f"{c}.Translation", w) for c, w in zip(columns, d_weights)])
        width = graph.vsum([(h, w) for h, w in zip(halves_world, weights)])
        aim = graph.aim(graph.make_transform(position), list(frame["tangent_axis"]), tangent,
                        list(frame["width_axis"]), width, primary_is_direction=True)
        return aim

    def joint_offset(self, bone, pin_rest):
        """Constant: joint_rest then pin_rest^-1, so offset * pin puts the bone on its pin."""
        rest = jeffy_math.plain(self.context.hierarchy.get_global_transform(
            graph_utils.make_key(unreal.RigElementType.BONE, bone), True))
        pin = jeffy_math.plain_pose(pin_rest)
        if pin is None:
            raise RuntimeError(f"{bone}: the pin's rest pose is missing")
        q, t = jeffy_math.compose(rest, jeffy_math.inverse(pin))
        return jeffy_math.transform(q, t)


class JeffyRibbonModule(_JeffyBase):
    module_type = "Jeffy.ribbon"
    solve_stage = "helpers"      # reads the limb's solved bones

    def read_recipe(self):
        return self.resolve_recipe_fields(
            {"Ribbon": None, "ControllerRecords": None, "ShapeTable": None, "ControlScale": 1.0},
            fallback_names={"Ribbon": ["ribbon"], "ControllerRecords": ["controller_records"],
                            "ShapeTable": ["shape_table"], "ControlScale": ["control_scale"]})

    def build(self):
        self.validate()
        recipe_data = self.read_recipe()
        ribbon = recipe_data.get("Ribbon") or {}
        if not ribbon.get("segments"):
            raise RuntimeError("the manifest carries no ribbon description (re-export from Maya)")
        if self.logger:
            self.logger.push(f"[JeffyRibbon] Building {self.name}")
        prefix = graph_utils.sanitize_name(self.name)
        graph = jeffy_math.Graph(self.context, f"{prefix}_Rb")
        expressions = _Expressions(self, graph)
        hierarchy, hc = self.context.hierarchy, self.context.hierarchy_controller
        self.context.claim_module_column(width=1400)
        base_joints = [str(j) for j in ribbon.get("base_joints") or []]
        scale = graph_utils.compute_chain_scale(hierarchy, base_joints or self.chain, fraction=0.12)

        # 1. The bend controls: each hangs from a null placed where its Maya
        #    group rests; the null is re-written every frame from the blend.
        controls, follow_nulls = [], []
        for key in sorted(ribbon.get("controls") or {}, key=lambda k: (not k.startswith("joint"), k)):
            entry = ribbon["controls"][key]
            record = self.record_named(recipe_data, entry["name"])
            rest = jeffy_math.pose_transform(entry.get("rest"))
            if record is None or rest is None:
                self.context._warn(f"{self.name}: bend control '{entry['name']}' has no record or rest; skipped.")
                continue
            null = graph_utils.create_follow_null(hierarchy, hc, f"RB_{graph_utils.sanitize_name(entry['name'])}_Follow",
                                                  rest, self.context.world_key())
            if not null:
                continue
            null_key = unreal.RigElementKey(type=unreal.RigElementType.NULL, name=str(null))
            name, _key, _driver = self.build_control(recipe_data, record, null_key, scale,
                                                     unreal.LinearColor(0.9, 0.5, 1.0, 1.0))
            controls.append(name)
            follow_nulls.append((null, entry["expr"]))
        for null, expr in follow_nulls:
            graph.set_transform("Null", null, expressions.pin(expr))

        # 2. The joints, segment by segment.
        frame = ribbon.get("pin_frame") or {}
        nodes = 0
        for segment in ribbon["segments"]:
            columns = [expressions.pin(c) for c in segment["columns"]]
            halves = [graph.rotate_vector(f"{c}.Rotation", list(h)) for c, h in zip(columns, segment["cv_half"])]
            for pin in segment["pins"]:
                bone = str(pin["joint"])
                if not hierarchy.contains(graph_utils.make_key(unreal.RigElementType.BONE, bone)):
                    self.context._warn(f"{self.name}: bone '{bone}' is not in the skeleton; skipped.")
                    continue
                aim = self.pin_frame_pins(graph, columns, pin["weights"], pin["d_weights"], halves, frame)
                joint = graph.make_absolute(self.joint_offset(bone, pin.get("pin_rest")), aim)
                graph.set_transform("Bone", bone, joint, propagate=False)
                nodes += 1

        # 3. bendyVis on the limb's settings control shows / hides the bend controls.
        self._visibility(graph, ribbon, controls)
        if self.logger:
            self.logger.log(f"[JeffyRibbon] {self.name}: {nodes} ribbon joint(s) on {len(ribbon['segments'])} "
                            f"segment(s), {len(controls)} bend control(s), {len(graph.nodes)} node(s).")
            self.logger.pop()
        return self.build_result(controls=controls, nodes=graph.nodes,
                                 attach_points={"root": self.chain[0], "tip": self.chain[-1]},
                                 outputs={"driven_bones": list(self.chain)}, recipe_data=recipe_data)

    def _visibility(self, graph, ribbon, controls):
        holder, attribute = ribbon.get("settings_control"), ribbon.get("visibility_attr")
        unit = jeffy_math.unit("SetControlVisibility")
        if not (holder and attribute and controls and unit):
            return
        try:
            value = graph.channel(holder, attribute)
            visible = graph.greater(value, 0.5)
            for control in controls:
                node = graph.node(("SetControlVisibility",), "Vis")
                graph_utils.set_key_pin(graph.controller, graph.model, node, ["Item"], "Control", control)
                graph.feed(node, ["bVisible", "Visible"], visible)
                tail = self.context.get_exec_tail() or graph_utils.find_forwards_solve_node_name(graph.model)
                graph_utils.connect_exec(graph.controller, graph.model, tail, node)
                self.context.set_exec_tail(node)
        except Exception as exc:
            self.context._warn(f"{self.name}: bendyVis not wired ({exc}).")


class JeffySplineModule(_JeffyBase):
    module_type = "Jeffy.spline"

    def read_recipe(self):
        return self.resolve_recipe_fields(
            {"Spline": None, "Switch": None, "ControllerRecords": None, "ShapeTable": None, "ControlScale": 1.0},
            fallback_names={"Spline": ["jeffy_spline"], "Switch": ["switch"], "ControllerRecords": ["controller_records"],
                            "ShapeTable": ["shape_table"], "ControlScale": ["control_scale"]})

    def build(self):
        self.validate()
        recipe_data = self.read_recipe()
        spline = recipe_data.get("Spline") or {}
        if not spline.get("pins"):
            raise RuntimeError("the manifest carries no spline description (re-export from Maya)")
        if self.logger:
            self.logger.push(f"[JeffySpline] Building {self.name}")
        prefix = graph_utils.sanitize_name(self.name)
        graph = jeffy_math.Graph(self.context, f"{prefix}_Sp")
        hierarchy, hc = self.context.hierarchy, self.context.hierarchy_controller
        self.context.claim_module_column(width=1600)
        scale = graph_utils.compute_chain_scale(hierarchy, self.chain, fraction=0.3)
        parent_key = self.default_parent_key()
        chain_mode = spline.get("mode") == "chain"
        wave = spline.get("wave") if chain_mode else None
        wave_zero = {c["name"]: c for c in (wave or {}).get("controls") or [] if c.get("zero_rest")}

        # 1. Controls, in Maya hierarchy order (parents first).
        records = sorted(self.records(recipe_data), key=lambda r: str(r.get("dag_path") or "").count("|"))
        controls, fk_pins, wave_nulls = [], {}, []
        for record in records:
            resolved = self.context.resolve_control_parent(record, parent_key)
            parent = resolved
            if record.get("name") in wave_zero:
                rest = jeffy_math.pose_transform(wave_zero[record["name"]]["zero_rest"])
                null = graph_utils.create_follow_null(hierarchy, hc, f"RB_{graph_utils.sanitize_name(record['name'])}_Wave",
                                                      rest, resolved) if rest else None
                if null:
                    parent = unreal.RigElementKey(type=unreal.RigElementType.NULL, name=str(null))
                    wave_nulls.append((null, wave_zero[record["name"]]))
            color = unreal.LinearColor(0.2, 0.8, 1.0, 1.0) if record.get("role") != "bone_driver" \
                else unreal.LinearColor(1.0, 0.65, 0.1, 1.0)
            name, _key, driver = self.build_control(recipe_data, record, parent, scale, color)
            controls.append(name)
            if record.get("role") == "bone_driver" and record.get("driven_bone"):
                getter = f"{prefix}_{graph_utils.sanitize_name(record['driven_bone'])}_GetFK"
                graph_utils.create_transform_getter(graph.controller, graph.model, getter, unreal.Vector2D(0, 0),
                                                    name, driver)
                graph.nodes.append(getter)
                fk_pins[str(record["driven_bone"]).split("|")[-1]] = f"{getter}.Transform"

        # 2. The wave (chain mode): each FK zero turns about its own axis.
        if wave and wave_nulls:
            self._wave(graph, wave, wave_nulls)

        # 3. The spline through the column centres.
        ik_names = [self.ue_control(n) for n in spline.get("ik_controls") or []]
        if any(n is None for n in ik_names):
            raise RuntimeError(f"IK controls not built: {spline.get('ik_controls')}")
        owners = [graph.get_control(n) for n in ik_names]
        points = []
        for column in spline["columns"]:
            owner = owners[int(column["owner"])]
            local = list(column["local"])
            if max(abs(c) for c in local) < 1e-9:
                points.append(f"{owner}.Translation")
            else:
                node = graph.node(("MathTransformTransformVector",), "TPoint")
                graph.feed(node, ["Transform"], owner)
                graph.feed(node, ["Vector", "Value"], local)
                points.append(graph.out(node))
        from_points = spline_ik_module._pick_spline_from_points_unit()
        position_unit = _spline_unit("Position", "Spline")
        tangent_unit = _spline_unit("Tangent", "Spline")
        length_unit = spline_ik_module._pick_get_length_of_spline_unit()
        if not (from_points and position_unit and tangent_unit and length_unit):
            raise RuntimeError("the Control Rig Spline nodes are not available (enable the ControlRigSpline plugin)")
        halves = [graph.rotate_vector(f"{owners[int(c['owner'])]}.Rotation", list(h))
                  for c, h in zip(spline["columns"], spline.get("cv_half") or [])]

        def make_spline(name, point_pins):
            node = f"{prefix}_{name}"
            graph_utils.create_unit_node(graph.controller, graph.model, node, from_points, unreal.Vector2D(0, 0))
            graph.nodes.append(node)
            spline_ik_module._populate_vector_array_from_pins(graph.controller, graph.model, node, "Points", point_pins)
            graph_utils.set_any_pin(graph.controller, graph.model, node, ["SplineMode", "Mode"], "BSpline")
            graph_utils.set_any_pin(graph.controller, graph.model, node, ["SamplesPerSegment"], str(SPLINE_SAMPLES))
            return f"{node}.Spline"

        # The strip's middle curve, and its edge (centre + half width): the
        # width direction at any u is edge(u) - middle(u), exactly as on the
        # NURBS strip (same knots, same weights).
        spline_pin = make_spline("Spline", points)
        edge_pin = make_spline("SplineEdge", [graph.vadd(p, h) for p, h in zip(points, halves)])

        def position_at(u, spline_source=spline_pin):
            node = graph.node((position_unit.__name__,), "Pos")
            graph.feed(node, ["Spline"], spline_source)
            graph.feed(node, ["U"], u)
            return graph.pin(node, ["Position", "Result"])

        def tangent_at(u):
            node = graph.node((tangent_unit.__name__,), "Tan")
            graph.feed(node, ["Spline"], spline_pin)
            graph.feed(node, ["U"], u)
            return graph.pin(node, ["Tangent", "Result"])

        length_node = graph.node((length_unit.__name__,), "Len")
        graph.feed(length_node, ["Spline"], spline_pin)
        length = graph.pin(length_node, ["Length", "Result"])

        # 4. Stretch factor and the chord walk (spline.py _length_chain).
        rig_scale = f"{owners[0]}.Scale3D.X"
        current = graph.div(length, rig_scale)
        ratio = graph.div(current, float(spline.get("rest_length") or 1.0))
        stretch = spline.get("stretch")
        if stretch:
            factor = graph.lerp(1.0, ratio, graph.channel(stretch["control"], stretch["attr"], stretch.get("value", 1.0)))
        else:
            factor = ratio
        step = graph.mul(factor, rig_scale)
        bone_lengths = [float(v) for v in spline.get("bone_lengths") or []]
        pins = spline["pins"]
        frame = spline.get("pin_frame") or {}
        previous = position_at(0.0)
        switch = None
        if chain_mode and spline.get("settings_control"):
            try:
                switch = graph.channel(spline["settings_control"], "ikFk", 1.0)
            except Exception as exc:
                self.context._warn(f"{self.name}: ikFk not found ({exc}); the chain stays in IK.")
        for index, pin in enumerate(pins):
            bone = str(pin["joint"])
            if index == 0:
                u = 0.0
                position = previous
            else:
                target = graph.mul(step, bone_lengths[index - 1])
                u = float(pin.get("u_rest") or 0.0)
                for _ in range(CHORD_ITERATIONS):
                    chord = graph.vdistance(position_at(u), previous)
                    chord_ahead = graph.vdistance(position_at(graph.add(u, CHORD_STEP)), previous)
                    slope = graph.sub(chord_ahead, chord)
                    delta = graph.mul(graph.div(graph.sub(chord, target), graph.select(
                        graph.greater(slope, 1e-9), slope, 1e-9)), CHORD_STEP)
                    u = graph.clamp(graph.sub(u, delta), 0.0, 1.0)
                position = position_at(u)
            tangent = tangent_at(u)
            width = graph.vsub(position_at(u, edge_pin), position)
            aim = graph.aim(graph.make_transform(position), list(frame["tangent_axis"]), tangent,
                            list(frame["width_axis"]), width, primary_is_direction=True)
            ik_pose = graph.make_absolute(self.joint_offset(bone, pin.get("pin_rest")), aim)
            final = ik_pose
            if chain_mode and bone in fk_pins:
                final = graph.tlerp(fk_pins[bone], ik_pose, switch if switch is not None else 1.0)
            graph.set_transform("Bone", bone, final, propagate=True)
            previous = position
        if self.logger:
            self.logger.log(f"[JeffySpline] {self.name} ({spline.get('mode')}): {len(pins)} joint(s) on a "
                            f"{len(points)}-point spline, {len(controls)} control(s), chord walk x{CHORD_ITERATIONS}, "
                            f"{len(graph.nodes)} node(s).")
            self.logger.pop()
        return self.build_result(controls=controls, nodes=graph.nodes,
                                 attach_points={"root": self.chain[0], "tip": self.chain[-1],
                                                "fk_tip_ctrl": controls[-1] if controls else None},
                                 outputs={"driven_bones": list(self.chain)}, recipe_data=recipe_data)

    def _wave(self, graph, wave, nulls):
        """angle_i = sin(fraction_i / waveLength * 360 - time * waveSpeed + waveOffset)
        * waveAmplitude * lerp(1, fraction_i, waveFalloff), about the zero's axis."""
        holder = wave["holder"]
        try:
            amplitude = graph.channel(holder, "waveAmplitude", 0.0)
            wave_length = graph.channel(holder, "waveLength", 1.0)
            speed = graph.channel(holder, "waveSpeed", 10.0)
            offset = graph.channel(holder, "waveOffset", 0.0)
            falloff = graph.channel(holder, "waveFalloff", 1.0)
        except Exception as exc:
            self.context._warn(f"{self.name}: wave attributes missing ({exc}); wave skipped.")
            return
        time = graph.time_frames(float(wave.get("fps") or 24.0))
        if time is None:
            self.context._warn(f"{self.name}: no time node in this engine build; the wave is frozen at frame "
                               f"{wave.get('time_frames', 0)}.")
            time = float(wave.get("time_frames") or 0.0)
        moving = graph.mul(time, speed)
        sine_unit = jeffy_math.unit("MathFloatSin")
        axis = list(wave.get("axis_unreal") or [0.0, 1.0, 0.0])
        for null, entry in nulls:
            fraction = float(entry.get("fraction") or 0.0)
            along = graph.mul(graph.div(fraction, wave_length), 360.0)
            phase = graph.add(graph.sub(along, moving), offset)
            sine = graph.node((sine_unit.__name__,), "Sin")
            graph.feed(sine, ["Value"], graph.mul(phase, math.pi / 180.0))
            weight = graph.lerp(1.0, fraction, falloff)
            angle = graph.mul(graph.mul(graph.out(sine), amplitude), weight)
            rotation = graph.quat_axis_angle(axis, graph.mul(angle, -math.pi / 180.0))
            pre = graph.make_transform([0.0, 0.0, 0.0], rotation=rotation)
            rest_local = graph.get_transform("Null", null, initial=True, local=True)
            graph.set_transform("Null", null, graph.make_absolute(pre, rest_local), local=True)
        if self.logger:
            self.logger.log(f"[JeffySpline] {self.name}: wave on {len(nulls)} FK control(s).")
