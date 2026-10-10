import json
from PySide6 import QtWidgets,QtTest
from motortester.gui import MainWindow
app=QtWidgets.QApplication([]);w=MainWindow();w.show();w.generate_demo();w.cursor_toggle.setChecked(True);style=w.styleSheet();results=[]
for px in (13,18,22):
 w.setStyleSheet(style.replace('font-size: 13px',f'font-size: {px}px'));w.resize(1000,710);QtTest.QTest.qWait(100)
 p=w.plot.geometry().bottom();t=w.trigger_label.geometry().top();results.append({'font_px':px,'plot_bottom':p,'trigger_top':t,'overlap':p>=t})
assert not results[0]['overlap'] and results[1]['overlap'] and results[2]['overlap']
print(json.dumps({'old_source':'2376d325d573b1aa3da6feaf8fddf05d2e1cb601','result':'EXPECTED OLD OVERLAP REPRODUCED','cases':results},indent=2));w.close()
