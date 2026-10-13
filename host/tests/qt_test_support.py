"""Qt event waits that also let Python slots run in native worker threads."""
import math
import time

from PySide6 import QtCore


def pump_events(milliseconds):
    """Wait the full duration, dispatching events/deferred deletes every 5 ms.

    PySide6 6.12's QTest.qWait retains the GIL, including while Qt dispatches
    deferred deletes. Use the GIL-releasing core bindings and Python sleep
    instead, so worker slots and native QObject cleanup can make progress.
    """
    deadline = time.monotonic() + milliseconds / 1000
    while True:
        now = time.monotonic()
        step_end = min(deadline, now + 0.005)
        budget_ms = min(5, max(0, math.ceil((step_end - now) * 1000)))
        QtCore.QCoreApplication.processEvents(
            QtCore.QEventLoop.ProcessEventsFlag.AllEvents, budget_ms)
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        time.sleep(max(0, step_end - time.monotonic()))
        if time.monotonic() >= deadline:
            return
