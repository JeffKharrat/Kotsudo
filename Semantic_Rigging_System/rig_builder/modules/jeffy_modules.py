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
"""

from typing import Any, cast

try:
    import unreal  # type: ignore
except ImportError:
    unreal = cast(Any, None)

from .. import driver_ops, graph_utils
from .fk_module import FKModule


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
