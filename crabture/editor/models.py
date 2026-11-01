"""
editor/models.py — ActionHistory & LayerManager for Crabture.

ActionHistory implements a command-pattern undo/redo stack.
LayerManager tracks named layer groups, each holding a list of QGraphicsItem refs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
#  ActionHistory
# ---------------------------------------------------------------------------

class ActionHistory:
    """Manages an undo/redo stack of action dicts.

    Each action dict must contain at minimum:
        "type"  – e.g. "add_item", "remove_item", "move_item", "crop"
        "item"  – the QGraphicsItem involved (may be None for crop actions)

    Additional keys depend on the action type.
    """

    def __init__(self) -> None:
        self._undo_stack: List[Dict[str, Any]] = []
        self._redo_stack: List[Dict[str, Any]] = []

    # -- public API --------------------------------------------------------

    def record_action(self, action_dict: Dict[str, Any]) -> None:
        """Record *action_dict* and clear the redo stack."""
        self._undo_stack.append(action_dict)
        self._redo_stack.clear()

    def get_undo_action(self) -> Optional[Dict[str, Any]]:
        """Pop the last action from the undo stack → push to redo."""
        if not self._undo_stack:
            return None
        action = self._undo_stack.pop()
        self._redo_stack.append(action)
        return action

    def get_redo_action(self) -> Optional[Dict[str, Any]]:
        """Pop from the redo stack → push to undo."""
        if not self._redo_stack:
            return None
        action = self._redo_stack.pop()
        self._undo_stack.append(action)
        return action

    def peek_undo(self) -> Optional[Dict[str, Any]]:
        return self._undo_stack[-1] if self._undo_stack else None

    def peek_redo(self) -> Optional[Dict[str, Any]]:
        return self._redo_stack[-1] if self._redo_stack else None

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()


# ---------------------------------------------------------------------------
#  LayerManager
# ---------------------------------------------------------------------------

@dataclass
class _Layer:
    """Internal representation of a single layer."""
    layer_id: str
    name: str
    visible: bool = True
    items: list = field(default_factory=list)  # List[QGraphicsItem]


class LayerManager:
    """Manages named layer groups.  Each layer holds a list of QGraphicsItem
    references whose Z-values are kept in sync with the layer ordering.

    Layers are stored bottom-to-top (index 0 = bottom layer).
    """

    BASE_Z = 1.0          # lowest layer z-value (z=0 reserved for bg pixmap)
    Z_STEP = 1000.0        # gap between neighbouring layers

    def __init__(self) -> None:
        self._layers: List[_Layer] = []
        # Create a default layer so there is always somewhere to put items.
        self.add_layer("Layer 1")

    # -- helpers -----------------------------------------------------------

    def _recalculate_z(self) -> None:
        """Set Z-values for every item based on its layer position."""
        for idx, layer in enumerate(self._layers):
            z = self.BASE_Z + idx * self.Z_STEP
            for item in layer.items:
                item.setZValue(z)

    def _find(self, layer_id: str) -> Optional[_Layer]:
        for layer in self._layers:
            if layer.layer_id == layer_id:
                return layer
        return None

    def _index(self, layer_id: str) -> int:
        for i, layer in enumerate(self._layers):
            if layer.layer_id == layer_id:
                return i
        return -1

    # -- public API --------------------------------------------------------

    def layer_names(self) -> List[tuple]:
        """Return [(layer_id, name, visible), …] top-to-bottom for UI."""
        return [
            (l.layer_id, l.name, l.visible)
            for l in reversed(self._layers)
        ]

    def add_layer(self, name: str) -> str:
        """Create a new layer on top. Returns the new layer_id."""
        lid = uuid.uuid4().hex[:8]
        self._layers.append(_Layer(layer_id=lid, name=name))
        self._recalculate_z()
        return lid

    def delete_layer(self, layer_id: str):
        """Remove *layer_id* and return its items (caller removes from scene)."""
        layer = self._find(layer_id)
        if layer is None:
            return []
        items = list(layer.items)
        self._layers.remove(layer)
        self._recalculate_z()
        return items

    def restore_layer(
        self,
        layer_id: str,
        name: str,
        index: Optional[int] = None,
        visible: bool = True,
    ) -> str:
        """Re-create a previously deleted layer with its original id.

        *index* is the bottom-to-top position (clamped; None = top).  If a
        layer with *layer_id* already exists this is a no-op.
        """
        if self._find(layer_id) is not None:
            return layer_id
        layer = _Layer(layer_id=layer_id, name=name, visible=visible)
        if index is None or index > len(self._layers):
            index = len(self._layers)
        self._layers.insert(max(0, index), layer)
        self._recalculate_z()
        return layer_id

    def layer_info(self, layer_id: Optional[str]) -> Optional[tuple]:
        """Return (name, index, visible) for *layer_id*, or None if unknown."""
        if layer_id is None:
            return None
        layer = self._find(layer_id)
        if layer is None:
            return None
        return (layer.name, self._index(layer_id), layer.visible)

    def layer_id_of(self, item) -> Optional[str]:
        """Return the id of the layer that contains *item*, if any."""
        for layer in self._layers:
            if item in layer.items:
                return layer.layer_id
        return None

    def is_layer_empty(self, layer_id: str) -> bool:
        layer = self._find(layer_id)
        return layer is not None and not layer.items

    def rename_layer(self, layer_id: str, new_name: str) -> None:
        layer = self._find(layer_id)
        if layer:
            layer.name = new_name

    def move_layer_up(self, layer_id: str) -> bool:
        idx = self._index(layer_id)
        if idx < 0 or idx >= len(self._layers) - 1:
            return False
        self._layers[idx], self._layers[idx + 1] = (
            self._layers[idx + 1],
            self._layers[idx],
        )
        self._recalculate_z()
        return True

    def move_layer_down(self, layer_id: str) -> bool:
        idx = self._index(layer_id)
        if idx <= 0:
            return False
        self._layers[idx], self._layers[idx - 1] = (
            self._layers[idx - 1],
            self._layers[idx],
        )
        self._recalculate_z()
        return True

    def toggle_visibility(self, layer_id: str) -> Optional[bool]:
        """Toggle visibility of all items in a layer. Returns new state."""
        layer = self._find(layer_id)
        if layer is None:
            return None
        layer.visible = not layer.visible
        for item in layer.items:
            item.setVisible(layer.visible)
        return layer.visible

    def add_item_to_layer(self, layer_id: str, item) -> None:
        """Register *item* in the given layer and set its Z-value."""
        layer = self._find(layer_id)
        if layer is None:
            return
        if item not in layer.items:
            layer.items.append(item)
        idx = self._index(layer_id)
        item.setZValue(self.BASE_Z + idx * self.Z_STEP)
        item.setVisible(layer.visible)

    def remove_item(self, item) -> None:
        """Remove *item* from whichever layer contains it."""
        for layer in self._layers:
            if item in layer.items:
                layer.items.remove(item)
                return

    def active_layer_id(self) -> Optional[str]:
        """Return the top-most layer id (convenience)."""
        if self._layers:
            return self._layers[-1].layer_id
        return None

    def get_items_for_layer(self, layer_id: str) -> list:
        layer = self._find(layer_id)
        return list(layer.items) if layer else []

    def clear(self) -> None:
        """Wipe all layers and items."""
        self._layers.clear()
