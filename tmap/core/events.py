from enum import Enum
from typing import Any, Callable, Dict, List
import inspect


class EventType(Enum):
    TICK_RECEIVED = "TICK_RECEIVED"
    BAR_SEALED = "BAR_SEALED"
    BAR_UPDATED = "BAR_UPDATED"
    DEPTH_UPDATED = "DEPTH_UPDATED"
    METRICS_UPDATED = "METRICS_UPDATED"
    FEED_STATUS = "FEED_STATUS"


class EventBus:
    """
    Lightweight, decoupled Pub/Sub Event Bus.
    Enables zero-coupling between network feeds, calculation engines, and UI layers.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[EventType, List[Callable[[Any], None]]] = {
            evt: [] for evt in EventType
        }

    def subscribe(self, event_type: EventType, callback: Callable[[Any], None]) -> None:
        """Register a callback for a specific event type."""
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable[[Any], None]) -> None:
        """Remove a callback from an event type."""
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)

    def emit(self, event_type: EventType, data: Any) -> None:
        """Synchronously broadcast event to all registered listeners."""
        for callback in self._subscribers[event_type]:
            try:
                callback(data)
            except Exception as e:
                # Prevent single subscriber fault from halting the event pipeline
                print(f"[EventBus Error] Exception in {callback.__name__ if hasattr(callback, '__name__') else callback}: {e}")

    def clear(self) -> None:
        """Clear all registered event listeners."""
        for evt in self._subscribers:
            self._subscribers[evt].clear()
