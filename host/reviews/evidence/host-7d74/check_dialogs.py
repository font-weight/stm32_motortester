from pathlib import Path
from unittest.mock import patch
from PySide6 import QtCore,QtWidgets
from motortester.gui import MainWindow
root=Path('/tmp/critic-b-host-7d74');app=QtWidgets.QApplication([]);w=MainWindow();w.show();app.processEvents();w.generate_demo()
(root/'exists.mtcap').write_bytes(b'existing')
def inspect_close():
    for widget in app.topLevelWidgets():
        if isinstance(widget,QtWidgets.QMessageBox):
            print('overwrite_buttons',[(b.text()) for b in widget.buttons()]);widget.grab().save(str(root/'overwrite-dialog.png'));widget.done(int(QtWidgets.QMessageBox.StandardButton.No))
QtCore.QTimer.singleShot(150,inspect_close)
with patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(root/'exists.mtcap'),'')):
    w.save_dialog()
w.close();app.processEvents()
