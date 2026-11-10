"""Modules for rigs built with the Jeffy toolkit (docs/jeffy-v010-support.md).

The Maya exporter describes every control of the supported Jeffy setups as
a standard Kotsudo controller record (tools/maya/jeffy_reader.py): its
parent controllers, its space switch (a blended space, one target per
space) and its driven offset groups (``length`` moving the next control).
So the Unreal side reuses the FK machinery:

* ``Jeffy.fk_chain`` -- one control per joint; the joint copies its
  control's world transform (Jeffy's contract: joint world = output world,
  and an FK output is its control).
* ``Jeffy.rig_base`` -- the global and root controls; ``globalScale``
  scales the global control (and everything under it); the root joint
  copies the root control.
* ``Jeffy.ikfk_limb`` -- Kotsudo's IK/FK limb (FK controls, Basic IK, the
  switch control), plus Jeffy's IK goal maths: soft IK, stretch, pin to the
  pole, upper / lower lengths and the pole twist (``ikfk_limb._ik_goal``).

The ribbon and spline modules are in jeffy_ribbon.py.
"""

import math
from typing import Any, cast

try:
    import unreal  # type: ignore
except ImportError:
    unreal = cast(Any, None)

from .. import driver_ops, graph_utils, jeffy_math
from .fk_module import FKModule
from .ikfk_module import IKFKModule


class JeffyFKChainModule(FKModule):
    module_type = "Jeffy.fk_chain"

    @classmethod
    def describe_contract(cls):
        contract = super().describe_contract()
        contract["module_type"] = cls.module_type
        contract["chain"]["roles"] = []
        contract["required_metadata"] = []
        return contract


class JeffyRigBaseModule(FKModule):
    module_type = "Jeffy.rig_base"
    # Global / root controls: before the modules whose spaces follow them.
    solve_stage = "spaces"

    @classmethod
    def describe_contract(cls):
        contract = super().describe_contract()
        contract["module_type"] = cls.module_type
        contract["chain"].update({"roles": [], "max_length": 1})
        contract["required_metadata"] = []
        return contract

    def build(self):
        recipe_data = self.read_recipe()
        records = [r for r in graph_utils.controller_records(recipe_data)
                   if str(r.get("role") or "") != "bone_driver"]
        hierarchy = self.context.hierarchy
        bone = self.chain[0]
        bone_transform = graph_utils.get_bone_global_transform(hierarchy, bone)
        scale = graph_utils.compute_chain_scale(hierarchy, self.chain, fraction=0.35, multiplier=1.0)
        # Controls that drive no joint (the global control): built first, so
        # the root control and every space targeting them find them.
        for record in records:
            name = self.context.control_name(record, f"{graph_utils.sanitize_name(self.name)}_"
                                                     f"{graph_utils.sanitize_name(record.get('name') or 'ctrl')}")
            graph_utils.build_record_control(
                self.context.rig, hierarchy, self.context.hierarchy_controller, recipe_data,
                self.context.resolve_control_parent(record, self.context.world_key()),
                name, bone, bone_transform, record,
                graph_utils.record_color(record, unreal.LinearColor(1.0, 0.9, 0.2, 1.0)),
                "Circle_Thick", (scale, scale, scale), None,
            )
            for attribute in record.get("attributes") or []:
                if attribute.get("name") == "globalScale":
                    self._wire_global_scale(record.get("name"), name)
        return super().build()

    def _wire_global_scale(self, maya_name, control_name):
        """globalScale -> uniform scale of the control, every evaluation."""
        controller, model = self.context.graph_controller, self.context.model
        base = f"RB_{graph_utils.sanitize_name(control_name)}_GlobalScale"
        try:
            pin = driver_ops.driver_pin(self.context, base, {"control": maya_name, "attr": "globalScale", "ops": []})
            vector_unit = next((u for u in (getattr(unreal, n, None) for n in (
                "RigVMFunction_MathVectorFromFloat", "RigUnit_MathVectorFromFloat")) if u is not None), None)
            set_unit = getattr(unreal, "RigUnit_SetScale", None)
            if pin is None or vector_unit is None or set_unit is None:
                raise RuntimeError("the channel or the vector / Set Scale nodes are not available")
            vector = f"{base}_Vector"
            graph_utils.create_unit_node(controller, model, vector, vector_unit, unreal.Vector2D(0, 0))
            graph_utils.connect_pins(controller, model, pin, f"{vector}.Value")
            setter = f"{base}_Set"
            graph_utils.create_unit_node(controller, model, setter, set_unit, unreal.Vector2D(0, 0))
            graph_utils.set_key_pin(controller, model, setter, ["Item"], "Control", control_name)
            graph_utils.set_any_pin(controller, model, setter, ["Space"], "LocalSpace")
            graph_utils.set_any_pin(controller, model, setter, ["bInitial", "Initial"], "False")
            graph_utils.set_any_pin(controller, model, setter, ["Weight"], "1.0")
            graph_utils.set_any_pin(controller, model, setter, ["bPropagateToChildren", "PropagateToChildren"],
                                    "True")
            if not graph_utils.connect_pins(controller, model, f"{vector}.Result", f"{setter}.Scale"):
                raise RuntimeError("could not connect the scale")
            tail = self.context.get_exec_tail() or graph_utils.find_forwards_solve_node_name(model)
            graph_utils.connect_exec(controller, model, tail, setter)
            self.context.set_exec_tail(setter)
            if self.logger:
                self.logger.log(f"[JeffyRigBase] '{control_name}': globalScale scales the control.")
        except Exception as exc:
            self.context._warn(f"globalScale of '{control_name}' not rebuilt ({exc}).")


class JeffyIKFKLimbModule(IKFKModule):
    """Jeffy's IK/FK limb on Kotsudo's IK/FK module.

    The FK controls, the IK control, the pole and the switch come from the
    controller records as for a tagged rig. What Jeffy adds is the IK goal:
    the effector the solver reaches for is not the IK control but a point
    computed from it (soft IK, stretch, pin), the bone lengths follow the
    ``upperLength`` / ``lowerLength`` / stretch / pin values, and ``twist``
    turns the pole plane about the limb axis. All of it is float / vector
    maths on the graph, with the formulas of ``ikfk_limb._ik_goal``::

        D    = distance(start, IK end) / rig scale
        L    = upper * upperLength + lower * lowerLength
        sd   = clamp(softness * L * 0.25, 1e-4, inf)
        Ds   = D > L - sd ? L - sd + sd * (1 - e^-((D - L + sd) / sd)) : D
        f    = lerp(1, D / Ds, stretch)
        goal = lerp(start + dir * Ds, IK end, 1 - (1 - stretch) * (1 - pin))
        bone lengths = [upper, lower] * f, then lerp(.., distance to / from the pole, pin)
    """

    module_type = "Jeffy.ikfk_limb"

    @classmethod
    def describe_contract(cls):
        contract = super().describe_contract()
        contract["module_type"] = cls.module_type
        contract["chain"]["roles"] = []
        contract["required_metadata"] = []
        return contract

    def read_recipe(self):
        data = super().read_recipe()
        extra = self.resolve_recipe_fields({"Limb": None}, fallback_names={"Limb": ["limb"]})
        data["Limb"] = extra.get("Limb") if isinstance(extra.get("Limb"), dict) else {}
        # Jeffy: in IK the end joint takes the IK control's rotation (its
        # IkEnd group), the chains blend in world space, no engine stretch.
        data["IKEndOrient"] = True
        data["RootLocalBlend"] = False
        data["EnableStretch"] = False
        return data

    def _build_two_bone_ik_solver(self, controller, model, hierarchy, hierarchy_controller, parent_key,
                                  module_prefix, ik_node, get_pole_node, ik_pole_ctrl, ik_col, pv_scale,
                                  recipe_data, effector_pin):
        limb = recipe_data.get("Limb") or {}
        graph = jeffy_math.Graph(self.context, f"{module_prefix}_Goal")
        goal = None
        try:
            goal = self._goal_inputs(graph, hierarchy, hierarchy_controller, limb, module_prefix)
        except Exception as exc:
            self.context._warn(f"{self.name}: IK goal maths not rebuilt ({exc}); the IK control is the effector.")
        pole_ctrl, pole_node = super()._build_two_bone_ik_solver(
            controller, model, hierarchy, hierarchy_controller, parent_key, module_prefix, ik_node,
            get_pole_node, ik_pole_ctrl, ik_col, pv_scale, recipe_data,
            goal["effector"] if goal else effector_pin,
        )
        if goal:
            try:
                self._finish_goal(graph, goal, limb, ik_node, pole_node)
            except Exception as exc:
                self.context._warn(f"{self.name}: bone lengths / pin / twist not rebuilt ({exc}).")
        return pole_ctrl, pole_node

    def _goal_inputs(self, graph, hierarchy, hierarchy_controller, limb, prefix):
        """The goal position and effector transform (everything but the pole)."""
        attributes = limb.get("attributes") or {}
        ik_name = limb.get("ik_control")
        effector_key = self.context.control_key_for_maya(ik_name)
        if effector_key is None:
            raise RuntimeError(f"IK control '{ik_name}' is not built")
        tip_transform = graph_utils.get_bone_global_transform(hierarchy, self.chain[-1])
        end_null = graph_utils.create_offset_driver(hierarchy, hierarchy_controller, effector_key,
                                                    f"{prefix}_IKEnd", tip_transform)
        if not end_null:
            raise RuntimeError("the IK end null could not be created")
        end = graph.get_transform("Null", end_null)
        start = graph.get_transform("Bone", self.chain[0])
        start_p, end_p = f"{start}.Translation", f"{end}.Translation"
        scale = f"{start}.Scale3D.X"
        positions = [graph_utils.get_bone_global_position(hierarchy, b) for b in self.chain]
        upper_rest = graph_utils.vector_length(graph_utils.vector_sub(positions[1], positions[0]))
        lower_rest = graph_utils.vector_length(graph_utils.vector_sub(positions[2], positions[1]))

        def channel(key, default):
            attribute = attributes.get(key)
            return graph.channel(ik_name, attribute, default) if attribute else float(default)

        distance_world = graph.vdistance(end_p, start_p)
        distance = graph.div(distance_world, scale)
        upper = graph.mul(upper_rest, channel("upper_length", 1.0))
        lower = graph.mul(lower_rest, channel("lower_length", 1.0))
        total = graph.add(upper, lower)
        if limb.get("soft"):
            softness = channel("softness", 0.0)
            soft_zone = graph.clamp(graph.mul(graph.mul(softness, total), 0.25), 1e-4, 1e9)
            hard = graph.sub(total, soft_zone)
            over = graph.sub(distance, hard)
            exponent = graph.clamp(graph.div(graph.mul(over, -1.0), soft_zone), -50.0, 50.0)
            falloff = graph.exp(exponent)
            soft_value = graph.add(hard, graph.mul(soft_zone, graph.sub(1.0, falloff)))
            reach = graph.select(graph.greater(distance, hard), soft_value, distance)
        else:
            reach = graph.select(graph.greater(distance, total), total, distance)
        reach = graph.clamp(reach, 1e-4, 1e12)
        factor = weight = stretch = pin = None
        if limb.get("stretch"):
            stretch = channel("stretch", 0.0)
            factor = graph.lerp(1.0, graph.div(distance, reach), stretch)
            weight = stretch
        if limb.get("pin"):
            pin = channel("pin", 0.0)
            if weight is None:
                weight = pin
            else:
                free = graph.mul(graph.sub(1.0, stretch), graph.sub(1.0, pin))
                weight = graph.sub(1.0, free)
        direction = graph.vsub(end_p, start_p)
        ratio = graph.div(graph.mul(reach, scale), graph.clamp(distance_world, 1e-4, 1e12))
        soft_point = graph.vadd(start_p, graph.vscale(direction, ratio))
        goal = soft_point if weight is None else graph.vlerp(soft_point, end_p, weight)
        effector = graph.make_transform(goal, rotation=f"{end}.Rotation")
        finals = [upper, lower] if factor is None else [graph.mul(upper, factor), graph.mul(lower, factor)]
        return {"effector": effector, "start": start_p, "end": end_p, "scale": scale, "direction": direction,
                "finals": finals, "pin": pin, "channel": channel}

    def _finish_goal(self, graph, goal, limb, ik_node, pole_node):
        """Bone lengths (stretch, pin) and the pole twist, once the pole exists."""
        controller, model = self.context.graph_controller, self.context.model
        pole_p = f"{pole_node}.Transform.Translation"
        finals = goal["finals"]
        if goal["pin"] is not None:
            to_pole = graph.div(graph.vdistance(goal["start"], pole_p), goal["scale"])
            from_pole = graph.div(graph.vdistance(pole_p, goal["end"]), goal["scale"])
            finals = [graph.lerp(finals[0], to_pole, goal["pin"]), graph.lerp(finals[1], from_pole, goal["pin"])]
        for pin_name, final in (("BoneALength", finals[0]), ("BoneBLength", finals[1])):
            if not graph_utils.connect_pins(controller, model, graph.mul(final, goal["scale"]), f"{ik_node}.{pin_name}"):
                raise RuntimeError(f"could not connect {pin_name}")
        twist_attribute = (limb.get("attributes") or {}).get("twist")
        if twist_attribute:
            twist = goal["channel"]("twist", 0.0)
            sign = float(limb.get("twist_sign_unreal", -1.0))
            angle = graph.mul(twist, sign * math.pi / 180.0)
            rotation = graph.quat_axis_angle(graph.vunit(goal["direction"]), angle)
            rotated = graph.vadd(goal["start"], graph.rotate_vector(rotation, graph.vsub(pole_p, goal["start"])))
            pole_pin = f"{ik_node}.PoleVector"
            try:
                controller.break_all_links(pole_pin, True)
            except Exception:
                try:
                    controller.break_link(pole_p, pole_pin)
                except Exception as exc:
                    raise RuntimeError(f"the pole vector pin could not be relinked ({exc})")
            if not graph_utils.connect_pins(controller, model, rotated, pole_pin):
                raise RuntimeError("could not connect the twisted pole vector")
        if self.logger:
            self.logger.log(f"[JeffyLimb] '{self.name}': soft IK / stretch / pin / lengths"
                            f"{' / twist' if twist_attribute else ''} rebuilt on the IK goal.")
