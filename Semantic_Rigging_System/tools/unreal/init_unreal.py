"""Runs at editor startup: adds the Kotsudo menu (see Semantic_Rigging_System/tools/unreal/kotsudo_menu.py)."""
import sys

KOTSUDO_TOOLS = r"E:/Kotsudo/Semantic_Rigging_System/tools/unreal"
if KOTSUDO_TOOLS not in sys.path:
    sys.path.append(KOTSUDO_TOOLS)
try:
    import kotsudo_menu
    kotsudo_menu.register()
except Exception as exc:  # never block the editor start
    import unreal
    unreal.log_warning(f"[Kotsudo] Menu not added: {exc}")
