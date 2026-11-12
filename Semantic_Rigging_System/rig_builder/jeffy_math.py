"""Vector / transform maths in the Control Rig graph for the Jeffy modules.

A thin layer over the RigVM math units (float, vector, quaternion,
transform) that names every node after the module (so a rebuild clears
them), picks the unit by name across engine builds and gives back the
output pin path of each operation. The modules write the formulas of
Jeffy's setups with it (soft IK, ribbons, spline chord walks), see
docs/jeffy-v010-support.md.

Every method takes and returns PIN PATHS (``"Node.Result"``) or python
constants; a constant is written into the node's default value, a pin is
linked. Transform constants are python tuples ``(quat_xyzw, translation)``.
"""

import math
from typing import Any, cast

try:
    import unreal  # type: ignore
except ImportError:
    unreal = cast(Any, None)

from . import graph_utils

_PREFIXES = ("RigVMFunction_", "RigUnit_")


def unit(*names):
    """The first unit struct found among ``names`` (with the RigVM prefixes)."""
    for name in names:
        for prefix in ("",) + _PREFIXES:
            found = getattr(unreal, prefix + name, None)
            if found is not None:
                return found
    return None


def vec(v):
    return "(X={:.9g},Y={:.9g},Z={:.9g})".format(*[float(c) for c in v])


def quat(q):
    return "(X={:.9g},Y={:.9g},Z={:.9g},W={:.9g})".format(*[float(c) for c in q])


def transform(q, t, s=(1.0, 1.0, 1.0)):
    return "(Rotation={},Translation={},Scale3D={})".format(quat(q), vec(t), vec(s))


def pose_transform(pose):
    """A manifest pose ({'position', 'axes'} in Unreal axes) as an unreal.Transform, or None."""
    rotation = graph_utils.record_rotation({"world_axes_unreal": (pose or {}).get("axes")})
    if rotation is None or not (pose or {}).get("position"):
        return None
    result = unreal.Transform(location=unreal.Vector(*[float(c) for c in pose["position"]]))
    result.rotation = rotation
    return result


def plain(transform_value):
    """(quat [x,y,z,w], translation [x,y,z]) of an unreal.Transform."""
    q = graph_utils.get_transform_rotation(transform_value)
    t = graph_utils.transform_to_location(transform_value)
    return [q.x, q.y, q.z, q.w], [t.x, t.y, t.z]


class Graph:
    """Places maths nodes for one module; names start with ``base``."""

    def __init__(self, context, base):
        self.context = context
        self.base = base
        self.controller = context.graph_controller
        self.model = context.model
        self.count = 0
        self.nodes = []
        self.missing = []

    # -- nodes ---------------------------------------------------------------
    def node(self, names, suffix=None):
        struct = unit(*names)
        if struct is None:
            self.missing.append(names[0])
            raise RuntimeError(f"Control Rig node '{names[0]}' is not available in this engine build")
        name = f"{self.base}_{self.count:03d}_{suffix or names[0]}"
        self.count += 1
        graph_utils.create_unit_node(self.controller, self.model, name, struct, unreal.Vector2D(0, 0))
        self.nodes.append(name)
        return name

    def pin(self, node, names):
        for name in names if isinstance(names, (list, tuple)) else (names,):
            if graph_utils.pin_exists(self.model, f"{node}.{name}"):
                return f"{node}.{name}"
        raise RuntimeError(f"{node}: none of the pins {names} exists")

    def feed(self, node, names, value):
        """Link a pin path or write a constant into the first existing pin."""
        pin = self.pin(node, names)
        if isinstance(value, str) and "." in value and graph_utils.pin_exists(self.model, value):
            if not graph_utils.connect_pins(self.controller, self.model, value, pin):
                raise RuntimeError(f"could not connect {value} -> {pin}")
        else:
            if isinstance(value, bool):
                value = "True" if value else "False"
            elif isinstance(value, (int, float)):
                value = f"{float(value):.9g}"
            elif isinstance(value, (list, tuple)):
                value = vec(value)
            graph_utils.set_pin_default(self.controller, self.model, pin, str(value))
        return pin

    def out(self, node, names=("Result", "Value", "ReturnValue", "Transform", "Output")):
        return self.pin(node, names)

    # -- floats ----------------------------------------------------------------
    def f2(self, op, a, b):
        node = self.node((f"MathFloat{op}",), op)
        self.feed(node, ["A"], a)
        self.feed(node, ["B"], b)
        return self.out(node)

    def add(self, a, b):
        return self.f2("Add", a, b)

    def sub(self, a, b):
        return self.f2("Sub", a, b)

    def mul(self, a, b):
        return self.f2("Mul", a, b)

    def div(self, a, b):
        return self.f2("Div", a, b)

    def clamp(self, value, low, high):
        node = self.node(("MathFloatClamp",), "Clamp")
        self.feed(node, ["Value"], value)
        self.feed(node, ["Minimum", "Min"], low)
        self.feed(node, ["Maximum", "Max"], high)
        return self.out(node)

    def lerp(self, a, b, t):
        node = self.node(("MathFloatLerp",), "Lerp")
        self.feed(node, ["A"], a)
        self.feed(node, ["B"], b)
        self.feed(node, ["T", "Alpha"], t)
        return self.out(node)

    def exp(self, value):
        node = self.node(("MathFloatExponential", "MathFloatExp"), "Exp")
        self.feed(node, ["Value"], value)
        return self.out(node)

    def greater(self, a, b):
        node = self.node(("MathFloatGreater",), "Greater")
        self.feed(node, ["A"], a)
        self.feed(node, ["B"], b)
        return self.out(node)

    def select(self, condition, if_true, if_false):
        node = self.node(("MathFloatSelectBool",), "Select")
        self.feed(node, ["Condition"], condition)
        self.feed(node, ["IfTrue"], if_true)
        self.feed(node, ["IfFalse"], if_false)
        return self.out(node)

    def constant(self, value):
        node = self.node(("MathFloatAdd",), "Const")
        self.feed(node, ["A"], float(value))
        self.feed(node, ["B"], 0.0)
        return self.out(node)

    # -- vectors -------------------------------------------------------------------
    def v2(self, op, a, b):
        node = self.node((f"MathVector{op}",), f"V{op}")
        self.feed(node, ["A"], a)
        self.feed(node, ["B"], b)
        return self.out(node)

    def vadd(self, a, b):
        return self.v2("Add", a, b)

    def vsub(self, a, b):
        return self.v2("Sub", a, b)

    def vscale(self, vector, factor):
        node = self.node(("MathVectorScale",), "VScale")
        self.feed(node, ["Value"], vector)
        self.feed(node, ["Factor"], factor)
        return self.out(node)

    def vlength(self, vector):
        node = self.node(("MathVectorLength",), "VLength")
        self.feed(node, ["Value"], vector)
        return self.out(node)

    def vdistance(self, a, b):
        return self.vlength(self.vsub(a, b))

    def vunit(self, vector):
        node = self.node(("MathVectorUnit",), "VUnit")
        self.feed(node, ["Value"], vector)
        return self.out(node)

    def vlerp(self, a, b, t):
        node = self.node(("MathVectorLerp",), "VLerp")
        self.feed(node, ["A"], a)
        self.feed(node, ["B"], b)
        self.feed(node, ["T", "Alpha"], t)
        return self.out(node)

    def vsum(self, terms):
        """Weighted sum of vector pins: [(pin or constant vector, weight pin or float)]."""
        total = None
        for vector, weight in terms:
            if isinstance(weight, (int, float)) and abs(float(weight)) < 1e-12:
                continue
            scaled = vector if (isinstance(weight, (int, float)) and abs(float(weight) - 1.0) < 1e-12) \
                else self.vscale(vector, weight)
            total = scaled if total is None else self.vadd(total, scaled)
        return total

    def rotate_vector(self, quaternion, vector):
        node = self.node(("MathQuaternionRotateVector",), "QRotV")
        self.feed(node, ["Quaternion", "Transform"], quaternion)
        self.feed(node, ["Vector"], vector)
        return self.out(node)

    def quat_axis_angle(self, axis, angle_radians):
        node = self.node(("MathQuaternionFromAxisAndAngle",), "QAxis")
        self.feed(node, ["Axis"], axis)
        self.feed(node, ["Angle"], angle_radians)
        return self.out(node)

    def quat_slerp(self, a, b, t):
        node = self.node(("MathQuaternionSlerp",), "QSlerp")
        self.feed(node, ["A"], a)
        self.feed(node, ["B"], b)
        self.feed(node, ["T", "Alpha"], t)
        return self.out(node)

    # -- transforms -------------------------------------------------------------------
    def make_transform(self, translation, rotation=None, scale=None):
        node = self.node(("MathTransformMake",), "TMake")
        self.feed(node, ["Translation"], translation)
        if rotation is not None:
            self.feed(node, ["Rotation"], rotation)
        if scale is not None:
            self.feed(node, ["Scale"], scale)
        return self.out(node)

    def tlerp(self, a, b, t):
        node = self.node(("MathTransformLerp",), "TLerp")
        self.feed(node, ["A"], a)
        self.feed(node, ["B"], b)
        self.feed(node, ["T", "Alpha"], t)
        return self.out(node)

    def make_absolute(self, local, parent):
        node = self.node(("MathTransformMakeAbsolute",), "TAbs")
        self.feed(node, ["Local"], local)
        self.feed(node, ["Parent"], parent)
        return self.pin(node, ["Global"])

    def make_relative(self, global_pin, parent):
        node = self.node(("MathTransformMakeRelative",), "TRel")
        self.feed(node, ["Global"], global_pin)
        self.feed(node, ["Parent"], parent)
        return self.pin(node, ["Local"])

    def aim(self, input_transform, primary_axis, primary_target, secondary_axis=None, secondary_direction=None,
            primary_is_direction=False):
        """Aim Math: the input transform turned so its ``primary_axis`` points
        at ``primary_target`` (a location, or a direction) and its
        ``secondary_axis`` toward ``secondary_direction``. Returns the pin."""
        node = self.node(("AimBoneMath", "AimMath"), "Aim")
        self.feed(node, ["InputTransform", "Input"], input_transform)
        self.feed(node, ["Primary.Weight"], 1.0)
        self.feed(node, ["Primary.Kind"], "Direction" if primary_is_direction else "Location")
        self.feed(node, ["Primary.Axis"], primary_axis)
        self.feed(node, ["Primary.Target"], primary_target)
        if secondary_axis is not None and secondary_direction is not None:
            self.feed(node, ["Secondary.Weight"], 1.0)
            self.feed(node, ["Secondary.Kind"], "Direction")
            self.feed(node, ["Secondary.Axis"], secondary_axis)
            self.feed(node, ["Secondary.Target"], secondary_direction)
        else:
            self.feed(node, ["Secondary.Weight"], 0.0)
        return self.out(node)

    # -- hierarchy --------------------------------------------------------------------
    def get_transform(self, item_type, name, initial=False, local=False):
        """Transform pin of a bone / null / control (global unless ``local``)."""
        node = self.node(("GetTransform",), f"Get{item_type}")
        graph_utils.set_key_pin(self.controller, self.model, node, ["Item"], item_type, name)
        graph_utils.set_any_pin(self.controller, self.model, node, ["Space"], "LocalSpace" if local else "GlobalSpace")
        graph_utils.set_any_pin(self.controller, self.model, node, ["bInitial", "Initial"], "True" if initial else "False")
        return f"{node}.Transform"

    def get_control(self, name):
        node = self.node(("GetControlTransform",), "GetCtl")
        graph_utils.set_pin_default(self.controller, self.model, f"{node}.Control", name)
        graph_utils.set_pin_default(self.controller, self.model, f"{node}.Space", "GlobalSpace")
        return f"{node}.Transform"

    def set_transform(self, item_type, name, value, propagate=True, local=False, weight=1.0):
        """Set Transform appended to the module's exec chain. Returns the node."""
        node = self.node(("SetTransform",), f"Set{item_type}")
        graph_utils.set_key_pin(self.controller, self.model, node, ["Item"], item_type, name)
        graph_utils.set_any_pin(self.controller, self.model, node, ["Space"], "LocalSpace" if local else "GlobalSpace")
        graph_utils.set_any_pin(self.controller, self.model, node, ["bInitial", "Initial"], "False")
        self.feed(node, ["Weight"], weight)
        graph_utils.set_any_pin(self.controller, self.model, node, ["bPropagateToChildren", "PropagateToChildren"],
                                "True" if propagate else "False")
        self.feed(node, ["Value", "Transform"], value)
        tail = self.context.get_exec_tail() or graph_utils.find_forwards_solve_node_name(self.model)
        graph_utils.connect_exec(self.controller, self.model, tail, node)
        self.context.set_exec_tail(node)
        return node

    def channel(self, control_name, attribute, fallback=None):
        """Float pin of a control's animation channel (Maya attribute), or a
        constant when the control has no such channel."""
        from . import driver_ops

        key = self.context.control_key_for_maya(control_name)
        if key is not None:
            pin = driver_ops.channel_pin(self.context, _ChannelBase(self), key, attribute)
            if pin is not None:
                return pin
        if fallback is None:
            raise RuntimeError(f"'{control_name}' has no channel '{attribute}'")
        return float(fallback)

    def time_frames(self, fps):
        """Pin: the rig's absolute time in frames, or None when no time unit exists.

        UE 5.6 has no graph node for the sequence's absolute time: Timeline
        and Get World Time count playback or world seconds, which do not
        follow Sequencer scrubbing and would not match Maya's frame. Only a
        true absolute-time unit is used; otherwise the caller freezes time."""
        for names in (("GetAbsoluteTime", "AbsoluteTime"),):
            struct = unit(*names)
            if struct is None:
                continue
            node = self.node(names, "Time")
            for out in ("AbsoluteTime", "Time", "Result"):
                if graph_utils.pin_exists(self.model, f"{node}.{out}"):
                    return self.mul(f"{node}.{out}", float(fps))
        return None


class _ChannelBase:
    """What driver_ops.channel_pin needs from an OpsGraph: a name base."""

    def __init__(self, graph):
        graph.count += 1
        self.base = f"{graph.base}_{graph.count:03d}"


def plain_pose(pose):
    """(quat, translation) of a manifest pose, or None."""
    value = pose_transform(pose)
    return plain(value) if value is not None else None


def compose(first, second):
    """(q, t) of ``first`` then ``second`` (Unreal: first * second)."""
    (qa, ta), (qb, tb) = first, second
    return _q_mul(qb, qa), [c + d for c, d in zip(_q_rotate(qb, ta), tb)]


def inverse(value):
    q, t = value
    qi = [-q[0], -q[1], -q[2], q[3]]
    return qi, [-c for c in _q_rotate(qi, t)]


def _q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def _q_rotate(q, v):
    x, y, z, w = q
    uvx, uvy, uvz = y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0]
    uuvx, uuvy, uuvz = y * uvz - z * uvy, z * uvx - x * uvz, x * uvy - y * uvx
    return [v[0] + 2.0 * (w * uvx + uuvx), v[1] + 2.0 * (w * uvy + uuvy), v[2] + 2.0 * (w * uvz + uuvz)]


def degrees_to_radians(graph, pin):
    return graph.mul(pin, math.pi / 180.0)
