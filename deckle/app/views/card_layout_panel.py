"""Card sheet size, fit, border, and bleed controls."""
from dataclasses import replace
from deckle.core.models import CardSettings

from deckle.core.card_layout import card_grid_summary


class CardLayoutPanel:
    def __init__(self,state,parent=None,on_change=None):
        from PySide6.QtWidgets import (QCheckBox,QComboBox,QDoubleSpinBox,QFormLayout,QLabel,QWidget)
        self.state,self.on_change=state,on_change or (lambda:None)
        self._boxes={}; self._labels={}
        self.widget=QWidget(parent); form=QFormLayout(self.widget); d=state.project.card_deck.settings if state.project.card_deck else CardSettings()
        self.error=QLabel(""); self.error.setWordWrap(True); self.error.setStyleSheet("color: #b00020"); form.addRow(self.error)
        def number(label,key,value,minimum=0.0,maximum=5000.0):
            box=QDoubleSpinBox(self.widget); box.setRange(minimum,maximum); box.setDecimals(3); box.setSingleStep(1); box.setValue(value); box.setSuffix(" pt"); box.editingFinished.connect(lambda k=key,b=box:self.set_value(k,b.value()*self._factor())); label_widget=QLabel(label,self.widget); form.addRow(label_widget,box); setattr(self,key,box); self._boxes[key]=box; self._labels[key]=(label_widget,label)
        self.unit=QComboBox(); self.unit.addItems(["pt","mm","in"]); self.unit.currentTextChanged.connect(self._unit_changed); form.addRow("Length units",self.unit)
        self.paper=QComboBox(); self.paper.addItem("Letter portrait",(612.0,792.0)); self.paper.addItem("Letter landscape",(792.0,612.0)); self.paper.addItem("A4 portrait",(595.275590551,841.88976378)); self.paper.addItem("A4 landscape",(841.88976378,595.275590551)); self.paper.currentIndexChanged.connect(self.set_paper); form.addRow("Sheet",self.paper)
        number("Finished width (pt)","width_pt",d.width_pt,0.001); number("Finished height (pt)","height_pt",d.height_pt,0.001)
        number("Side margins (pt)","margin_x_pt",d.margin_x_pt); number("Top/bottom margins (pt)","margin_y_pt",d.margin_y_pt)
        number("Horizontal gap (pt)","gap_x_pt",d.gap_x_pt); number("Vertical gap (pt)","gap_y_pt",d.gap_y_pt)
        self.fit=QComboBox(); self.fit.addItem("Fit entire image","contain"); self.fit.addItem("Fill (crop)","cover"); self.fit.setCurrentIndex(0 if d.fit=="contain" else 1); self.fit.currentIndexChanged.connect(lambda:self.set_value("fit",self.fit.currentData())); form.addRow("Image fit",self.fit)
        self.border=QCheckBox("Black border"); self.border.setChecked(d.border_enabled); self.border.toggled.connect(lambda v:self.set_value("border_enabled",v)); form.addRow(self.border)
        number("Inside cut border (pt)","border_width_pt",d.border_width_pt); number("Black beyond cut (pt)","bleed_pt",d.bleed_pt)
        self.cut_marks=QCheckBox("Cut marks"); self.cut_marks.setChecked(d.cut_marks); self.cut_marks.toggled.connect(lambda v:self.set_value("cut_marks",v)); form.addRow(self.cut_marks)
        self.hint=QLabel("Black beyond the cut helps cover small front/back shifts. More allowance may fit fewer cards on a sheet."); self.hint.setWordWrap(True); form.addRow(self.hint)
        self.capacity=QLabel(""); form.addRow("Sheet capacity",self.capacity)
        self.refresh()

    def _factor(self):
        return {"pt":1.0,"mm":72.0/25.4,"in":72.0}[self.unit.currentText()]

    def _unit_changed(self,unit):
        factor=self._factor()
        for key,box in self._boxes.items():
            box.blockSignals(True); box.setValue(getattr(self.state.project.card_deck.settings,key)/factor); box.setSuffix(" "+unit); box.blockSignals(False)
            label,original=self._labels[key]; label.setText(original.replace("(pt)",f"({unit})"))

    def set_value(self,key,value):
        try:
            deck=self.state.project.card_deck
            settings=replace(deck.settings,**{key:value})
            from deckle.core.cards import validate_card_deck
            validate_card_deck(replace(deck,settings=settings))
            self.state.mutate(lambda p:replace(p,card_deck=replace(p.card_deck,settings=settings)))
            self.error.setText("")
            self.on_change(); self.refresh()
        except ValueError as exc:
            self.error.setText(str(exc))
            self.refresh()

    def set_paper(self,index):
        paper=self.paper.currentData()
        if paper:self.state.mutate(lambda p:replace(p,layout=replace(p.layout,paper=paper))); self.on_change(); self.refresh()

    def refresh(self):
        if self.state.project.card_deck is None: return
        d=self.state.project.card_deck.settings
        factor=self._factor()
        for key,box in self._boxes.items():
            box.blockSignals(True); box.setValue(getattr(d,key)/factor); box.blockSignals(False)
        self.border_width_pt.setEnabled(d.border_enabled); self.bleed_pt.setEnabled(d.border_enabled)
        self.paper.blockSignals(True)
        idx=next((i for i in range(self.paper.count()) if self.paper.itemData(i)==self.state.project.layout.paper),-1)
        custom_index=next((i for i in range(self.paper.count()) if self.paper.itemData(i) is None),-1)
        if idx<0:
            if custom_index<0:
                self.paper.addItem("Custom current size",None)
                custom_index=self.paper.count()-1
            self.paper.setItemText(custom_index, f"Custom ({self.state.project.layout.paper[0]:g} × {self.state.project.layout.paper[1]:g} pt)")
            self.paper.setCurrentIndex(custom_index)
        else:
            if custom_index>=0:self.paper.removeItem(custom_index)
            self.paper.setCurrentIndex(idx)
        self.paper.blockSignals(False)
        try:
            summary=card_grid_summary(self.state.project.card_deck,self.state.project.layout.paper)
            self.capacity.setText(f"{summary.columns} × {summary.rows} = {summary.capacity} cards per sheet; {summary.sheet_count} sheet(s)")
        except ValueError as exc:self.capacity.setText(str(exc))
